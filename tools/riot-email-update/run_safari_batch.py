#!/usr/bin/env python3
"""Batch Riot email updates via macOS Safari AppleScript.

Reads accounts from a CSV, runs safari_riot_email.applescript one-by-one,
appends successes to success.txt and failures to failed.txt.

CSV columns (same as data/tasks.csv.example):
  riot_username,riot_password,imap_email,imap_app_password,new_email,
  imap_host,imap_port,proxy_index,email_change_url

Usage (on a Mac with Safari JS-from-Apple-Events enabled):
  python3 run_safari_batch.py data/tasks.csv
  python3 run_safari_batch.py data/tasks.csv --success success.txt --failed failed.txt

Notes:
  - Must run on macOS (Safari). This cloud Linux environment cannot drive Safari.
  - Each row gets a fresh Safari window; results append to the output files.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from investigate_log import (
    append_block,
    extract_log_path_from_output,
    new_log_path,
    write_line,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_ENTRY = (
    "https://docs.qq.com/scenario/link.html?"
    "url=https%3A%2F%2Faccount.riotgames.com%2F"
    "&pid=300000000%24KrVGtggzglZK"
    "&cid=144115210422737002"
    "&nlc=1"
)

IMAP_HINTS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "mac.com": "imap.mail.me.com",
    "outlook.com": "imap-mail.outlook.com",
    "hotmail.com": "imap-mail.outlook.com",
    "live.com": "imap-mail.outlook.com",
    "yahoo.com": "imap.mail.yahoo.com",
}


def derive_imap_host(email: str) -> str:
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    return IMAP_HINTS.get(domain, f"imap.{domain}" if domain else "")


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv as _load

        _load(env_path, override=False)
    except Exception:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def read_tasks(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(f"CSV has no header: {path}")
        required = {
            "riot_username",
            "riot_password",
            "imap_email",
            "imap_app_password",
            "new_email",
        }
        missing = required - {h.strip() for h in reader.fieldnames if h}
        if missing:
            raise SystemExit(f"CSV missing columns: {sorted(missing)}")
        for i, raw in enumerate(reader, start=2):
            row = {((k or "").strip()): (v or "").strip() for k, v in raw.items()}
            if not row.get("riot_username") or row["riot_username"].startswith("#"):
                continue
            for key in required:
                if not row.get(key):
                    raise SystemExit(f"Row {i}: missing {key}")
            rows.append(row)
    return rows


def account_line(row: dict[str, str]) -> str:
    """Compact line written to success.txt / failed.txt."""
    return "\t".join(
        [
            row.get("riot_username", ""),
            row.get("riot_password", ""),
            row.get("imap_email", ""),
            row.get("imap_app_password", ""),
            row.get("new_email", ""),
            row.get("imap_host", "") or derive_imap_host(row.get("imap_email", "")),
        ]
    )


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def run_one(
    row: dict[str, str], *, entry_url: str, skip_email: bool
) -> tuple[bool, str, str]:
    """Returns (ok, reason, investigation_log_path)."""
    imap_host = row.get("imap_host") or derive_imap_host(row["imap_email"])
    imap_port = row.get("imap_port") or "993"

    log_path = new_log_path(tag=row["riot_username"], prefix="safari")
    write_line(log_path, f"batch start {row['riot_username']} → {row['new_email']}")
    write_line(log_path, f"imap={row['imap_email']}@{imap_host}:{imap_port}")
    write_line(log_path, f"entry_url={entry_url}")
    write_line(log_path, f"skip_email_change={int(skip_email)}")

    env = dict(os.environ)
    env.update(
        {
            "TOOL_DIR": str(ROOT),
            "IMAP_HOST": imap_host,
            "IMAP_PORT": imap_port,
            "IMAP_USER": row["imap_email"],
            "IMAP_PASSWORD": row["imap_app_password"],
            "IMAP_SSL": "true",
            "RIOT_USERNAME": row["riot_username"],
            "RIOT_PASSWORD": row["riot_password"],
            "NEW_EMAIL": row["new_email"],
            "SAFARI_BATCH": "1",
            "SAFARI_LOG_PATH": str(log_path),
        }
    )

    # Write a task file — more reliable than osascript argv / system attribute.
    task_path = ROOT / ".safari_current_task"
    task_lines = [
        f"riot_username={row['riot_username']}",
        f"riot_password={row['riot_password']}",
        f"new_email={row['new_email']}",
        f"imap_email={row['imap_email']}",
        f"imap_app_password={row['imap_app_password']}",
        f"imap_host={imap_host}",
        f"imap_port={imap_port}",
        f"entry_url={entry_url}",
        f"skip_email_change={'1' if skip_email else '0'}",
    ]
    task_path.write_text("\n".join(task_lines) + "\n", encoding="utf-8")
    env["SAFARI_TASK_FILE"] = str(task_path)

    cmd = [
        "osascript",
        str(ROOT / "safari_riot_email.applescript"),
        "--",
        "--batch",
        "--task-file",
        str(task_path),
    ]

    print(
        f"\n{'=' * 60}\n"
        f"Safari batch: {row['riot_username']} → {row['new_email']}\n"
        f"  IMAP {row['imap_email']} @ {imap_host}\n"
        f"  task-file: {task_path}\n"
        f"  investigate log: {log_path}\n"
        f"{'=' * 60}",
        flush=True,
    )
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
            timeout=float(os.getenv("SAFARI_TASK_TIMEOUT") or "600"),
        )
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or "") + "\n" + (exc.stderr or "")).strip()
        if out:
            append_block(log_path, "osascript (timeout)", out)
        write_line(log_path, "RESULT: timeout", level="ERROR")
        print(f"Investigation log: {log_path}", flush=True)
        return False, "timeout", str(log_path)
    except FileNotFoundError:
        write_line(log_path, "RESULT: osascript not found", level="ERROR")
        print(f"Investigation log: {log_path}", flush=True)
        return False, "osascript not found (macOS only)", str(log_path)
    finally:
        try:
            task_path.unlink(missing_ok=True)
        except Exception:
            pass

    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if out:
        append_block(log_path, "osascript output", out)
        # Keep console readable — last lines matter most
        lines = out.splitlines()
        print("\n".join(lines[-40:]), flush=True)

    reported = extract_log_path_from_output(out)
    if reported:
        log_path = Path(reported)

    if proc.returncode == 0 and (
        "SUCCESS" in out.upper() or "done" in (proc.stdout or "").lower()
    ):
        write_line(log_path, "RESULT: success")
        return True, "ok", str(log_path)

    reason = "osascript_failed"
    for marker in (
        "bad_creds",
        "rejected username/password",
        "hCaptcha",
        "MFA required",
        "SAVE AND VERIFY",
        "No Verify Your Email link",
        "log-out-everywhere-button",
        "LOG OUT EVERYWHERE",
        "modal_close-btn",
        "Confirm modal",
        "Timed out",
        "Allow JavaScript",
        "Cloudflare",
        "cloudflare",
    ):
        if marker.lower() in out.lower():
            reason = marker.replace(" ", "_")[:80]
            break
    if proc.returncode != 0:
        reason = f"{reason}:rc={proc.returncode}"
    write_line(log_path, f"RESULT: failed ({reason})", level="ERROR")
    print(f"Investigation log: {log_path}", flush=True)
    return False, reason, str(log_path)


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Batch Safari Riot email updates")
    ap.add_argument("csv", nargs="?", default=str(ROOT / "data" / "tasks.csv"))
    ap.add_argument("--success", default=str(ROOT / "success.txt"))
    ap.add_argument("--failed", default=str(ROOT / "failed.txt"))
    ap.add_argument(
        "--entry-url",
        default=os.getenv("LOGIN_ENTRY_URL")
        or os.getenv("LOGIN_URL")
        or DEFAULT_ENTRY,
    )
    ap.add_argument(
        "--skip-email-change",
        action="store_true",
        help="Only login each account (still records success/fail)",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=float(os.getenv("SAFARI_BATCH_DELAY") or "25"),
        help="Base seconds between accounts (jitter added; default 25 to reduce Cloudflare)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if sys.platform != "darwin" and not args.dry_run:
        print(
            "ERROR: Safari batch must run on macOS (this host is "
            f"{sys.platform}).\n"
            "  Put your CSV in data/tasks.csv on the Mac, then:\n"
            "    python3 run_safari_batch.py data/tasks.csv",
            file=sys.stderr,
        )
        return 2

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = (ROOT / csv_path).resolve()
    if not csv_path.exists():
        raise SystemExit(
            f"CSV not found: {csv_path}\n"
            f"  Copy data/tasks.csv.example → data/tasks.csv and fill rows."
        )

    tasks = read_tasks(csv_path)
    if not tasks:
        raise SystemExit(
            f"No account rows found in {csv_path}\n"
            f"  Add rows under the header (riot_username,riot_password,...).\n"
            f"  See data/tasks.csv.example"
        )
    success_path = Path(args.success)
    failed_path = Path(args.failed)
    if not success_path.is_absolute():
        success_path = ROOT / success_path
    if not failed_path.is_absolute():
        failed_path = ROOT / failed_path

    print(f"Loaded {len(tasks)} task(s) from {csv_path}")
    print(f"  success → {success_path}")
    print(f"  failed  → {failed_path}")
    print(f"  logs    → {ROOT / 'debug' / 'logs'}")
    if args.dry_run:
        for i, t in enumerate(tasks, 1):
            print(f"  {i}. {t['riot_username']} → {t['new_email']}")
        print("--dry-run: no Safari launched")
        return 0

    ok_n = fail_n = 0
    for i, row in enumerate(tasks, 1):
        print(f"\n>>> TASK {i}/{len(tasks)}", flush=True)
        ok, reason, log_path = run_one(
            row, entry_url=args.entry_url, skip_email=args.skip_email_change
        )
        line = account_line(row)
        if ok:
            append_line(success_path, line)
            ok_n += 1
            print(f"SUCCESS → {success_path.name}: {row['riot_username']}", flush=True)
            if log_path:
                print(f"  log → {log_path}", flush=True)
        else:
            append_line(failed_path, f"{line}\t{reason}\t{log_path}")
            fail_n += 1
            print(
                f"FAILED  → {failed_path.name}: {row['riot_username']} ({reason})",
                flush=True,
            )
            if log_path:
                print(f"  investigation log → {log_path}", flush=True)
        if i < len(tasks) and args.delay > 0:
            # Jitter between accounts — steady 3s cadence is a CF trigger.
            pause = max(5.0, float(args.delay) + random.uniform(3.0, 12.0))
            print(f"Cool-down {pause:.0f}s before next account…", flush=True)
            time.sleep(pause)

    print(f"\nBatch done: {ok_n} success, {fail_n} failed (of {len(tasks)})")
    print(f"Investigation logs: {ROOT / 'debug' / 'logs'}")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
