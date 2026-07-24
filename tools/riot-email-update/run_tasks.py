#!/usr/bin/env python3
"""
Batch runner for Riot email-update tasks.

Reads a CSV where each row is one account task, then runs the email-update
flow for each row in MANUAL captcha mode (you solve the hCaptcha in the
browser; login form, MFA via IMAP, and the email change are automated).

CSV columns (header required; see data/tasks.csv.example):
  riot_username        Riot login (username or email)      [required]
  riot_password        Riot password                       [required]
  imap_email           Mailbox that receives Riot codes     [required]
  imap_app_password    App-specific password for that mailbox [required]
  new_email            Email address to change to           [required]
  imap_host            IMAP server (optional; auto by domain)
  imap_port            IMAP port (optional; default 993)
  proxy_index          Proxy list index for this task (optional)

Blank optional cells are fine. Lines starting with '#' are ignored.

Usage:
  python run_tasks.py                 # uses data/tasks.csv
  python run_tasks.py path/to/file.csv
  python run_tasks.py --dry-run       # validate CSV without launching
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = ROOT / "data" / "tasks.csv"
RESULTS_CSV = ROOT / "data" / "tasks_results.csv"

REQUIRED = ("riot_username", "riot_password", "imap_email", "imap_app_password", "new_email")

# Auto-derive IMAP host from the mailbox domain when imap_host is blank.
IMAP_HOSTS = {
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "mac.com": "imap.mail.me.com",
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "msn.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "ymail.com": "imap.mail.yahoo.com",
    "aol.com": "imap.aol.com",
}


def derive_imap_host(email: str) -> str:
    domain = (email.rsplit("@", 1)[-1] if "@" in email else "").strip().lower()
    if not domain:
        return ""
    return IMAP_HOSTS.get(domain, f"imap.{domain}")


def read_tasks(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        # Strip comment lines before the CSV parser sees them.
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    if reader.fieldnames is None:
        raise SystemExit(f"{path}: empty or missing header row")
    headers = {(h or "").strip().lower() for h in reader.fieldnames}
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        raise SystemExit(
            f"{path}: missing required column(s): {', '.join(missing)}\n"
            f"  header must include: {', '.join(REQUIRED)}"
        )
    for i, raw in enumerate(reader, start=1):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not any(row.get(c) for c in REQUIRED):
            continue  # blank line
        blanks = [c for c in REQUIRED if not row.get(c)]
        if blanks:
            print(f"  ! row {i}: skipping — missing {', '.join(blanks)}", flush=True)
            continue
        rows.append(row)
    return rows


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_env(row: dict[str, str], *, headed: bool) -> dict[str, str]:
    env = dict(os.environ)
    imap_host = row.get("imap_host") or derive_imap_host(row["imap_email"])
    env.update(
        {
            "RIOT_USERNAME": row["riot_username"],
            "RIOT_PASSWORD": row["riot_password"],
            "NEW_EMAIL": row["new_email"],
            "IMAP_HOST": imap_host,
            "IMAP_PORT": row.get("imap_port") or "993",
            "IMAP_USER": row["imap_email"],
            "IMAP_PASSWORD": row["imap_app_password"],
            "IMAP_FOLDER": row.get("imap_folder") or "INBOX",
            "IMAP_SSL": "true",
            # Prefer .env CAPTCHA_PROVIDER (aycd / manual / vision / …)
            "CAPTCHA_PROVIDER": env.get("CAPTCHA_PROVIDER") or "manual",
            "NONINTERACTIVE": "1",
            "HEADED": "true" if headed else "false",
            "PROXY_ATTEMPTS": env.get("PROXY_ATTEMPTS") or "1",
        }
    )
    if row.get("proxy_index"):
        env["PROXY_INDEX"] = row["proxy_index"]
    if row.get("email_change_url"):
        env["EMAIL_CHANGE_URL"] = row["email_change_url"]
    return env


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch Riot email-update runner (manual captcha).")
    ap.add_argument("csv", nargs="?", default=str(DEFAULT_CSV), help="Path to tasks CSV")
    ap.add_argument("--dry-run", action="store_true", help="Validate CSV; do not launch")
    ap.add_argument("--imap-timeout", type=int, default=180, help="Seconds to wait for MFA code")
    ap.add_argument(
        "--headed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show browser window (default: HEADED env, else true). Use --no-headed / --headless.",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Shortcut for --no-headed (Chromium with no window).",
    )
    args = ap.parse_args()
    if args.headless:
        headed = False
    elif args.headed is not None:
        headed = bool(args.headed)
    else:
        headed = _env_bool("HEADED", True)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(
            f"Task file not found: {csv_path}\n"
            f"  Copy data/tasks.csv.example → data/tasks.csv and fill in your rows."
        )

    tasks = read_tasks(csv_path)
    if not tasks:
        raise SystemExit(f"No valid task rows in {csv_path}")

    print(f"Loaded {len(tasks)} task(s) from {csv_path}")
    for i, t in enumerate(tasks, 1):
        host = t.get("imap_host") or derive_imap_host(t["imap_email"])
        print(f"  {i}. {t['riot_username']} → {t['new_email']}  (imap {t['imap_email']} @ {host})")
    if args.dry_run:
        print("\n--dry-run: CSV valid. No tasks launched.")
        return 0

    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str]] = []
    for i, row in enumerate(tasks, 1):
        print("\n" + "#" * 70)
        print(f"# TASK {i}/{len(tasks)}: {row['riot_username']} → {row['new_email']}")
        print("#" * 70, flush=True)
        env = build_env(row, headed=headed)
        captcha = (env.get("CAPTCHA_PROVIDER") or "manual").strip().lower()
        cmd = [
            sys.executable,
            str(ROOT / "update_email.py"),
            "--headed" if headed else "--no-headed",
            "--captcha-provider", captcha,
            "--username", row["riot_username"],
            "--new-email", row["new_email"],
            "--imap-timeout", str(args.imap_timeout),
        ]
        print(f"# browser={'headed' if headed else 'headless'} captcha={captcha}", flush=True)
        if row.get("email_change_url"):
            cmd.extend(["--email-change-url", row["email_change_url"]])
        elif env.get("EMAIL_CHANGE_URL"):
            cmd.extend(["--email-change-url", env["EMAIL_CHANGE_URL"]])
        t0 = time.time()
        try:
            rc = subprocess.call(cmd, env=env, cwd=str(ROOT))
        except KeyboardInterrupt:
            print("\nInterrupted — stopping batch.")
            results.append({"riot_username": row["riot_username"], "new_email": row["new_email"],
                            "status": "interrupted", "seconds": round(time.time() - t0, 1)})
            break
        status = "ok" if rc == 0 else f"failed(rc={rc})"
        print(f"# TASK {i} result: {status} ({round(time.time()-t0,1)}s)", flush=True)
        results.append({"riot_username": row["riot_username"], "new_email": row["new_email"],
                        "status": status, "seconds": round(time.time() - t0, 1)})

    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["riot_username", "new_email", "status", "seconds"])
        w.writeheader()
        w.writerows(results)

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nBatch done: {ok}/{len(results)} succeeded. Results → {RESULTS_CSV}")
    return 0 if ok == len(results) and results else 2


if __name__ == "__main__":
    raise SystemExit(main())
