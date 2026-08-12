#!/usr/bin/env python3
"""Batch Riot email updates via macOS Safari AppleScript.

Reads accounts from a CSV, runs safari_riot_email.applescript one-by-one.

On SUCCESS:
  - append to success.txt
  - remove that row from tasks.csv (so the queue shrinks as you go)

On FAILURE:
  - append a paste-ready CSV row to failed.txt (same columns as tasks.csv)
  - append reason/log to failed_reasons.txt

Stop immediately (does not continue remaining tasks):
  - Ctrl+C / Terminal Stop
  - double-click STOP_BATCH.command
  - touch .safari_batch_stop in the toolkit folder

CSV columns (same as data/tasks.csv.example):
  riot_username,riot_password,new_password,imap_email,imap_app_password,new_email,
  imap_host,imap_port,proxy_index,email_change_url,change_password

  change_password: 1 = change password after email, 0 = skip password change.
  Missing change_password defaults to 1. Global SKIP_PASSWORD_CHANGE=1 forces off.

Flow per account: login → change email → verify → (optional) change password → logout.

Usage (on a Mac with Safari JS-from-Apple-Events enabled):
  python3 run_safari_batch.py data/tasks.csv
  python3 run_safari_batch.py data/tasks.csv --success success.txt --failed failed.txt
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import signal
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
STOP_FLAG = ROOT / ".safari_batch_stop"
PID_FILE = ROOT / ".safari_batch.pid"
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

_stop_requested = False
_current_proc: subprocess.Popen[str] | None = None


def derive_imap_host(email: str) -> str:
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    return IMAP_HINTS.get(domain, f"imap.{domain}" if domain else "")


def request_stop(reason: str = "stop") -> None:
    global _stop_requested
    if _stop_requested:
        return
    _stop_requested = True
    print(f"\n⏹  Stop requested ({reason}) — aborting batch, killing current task…", flush=True)
    try:
        STOP_FLAG.write_text(f"stop {time.time()}\n", encoding="utf-8")
    except Exception:
        pass
    kill_current_task()


def stop_requested() -> bool:
    if _stop_requested:
        return True
    try:
        if STOP_FLAG.exists():
            request_stop("stop-file")
            return True
    except Exception:
        pass
    return False


def clear_stop_flag() -> None:
    try:
        STOP_FLAG.unlink(missing_ok=True)
    except Exception:
        pass


def write_pid_file() -> None:
    try:
        PID_FILE.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    except Exception:
        pass


def clear_pid_file() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def kill_current_task() -> None:
    """Kill the running osascript (and its process group) immediately."""
    global _current_proc
    proc = _current_proc
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
        # Started in its own session — kill the whole group.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=1.5)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    finally:
        _current_proc = None


def kill_helper_processes() -> None:
    """Best-effort sweep of IMAP helpers / leftover osascript for this toolkit."""
    patterns = (
        "safari_riot_email.applescript",
        "fetch_riot_verify_link.py",
        "fetch_riot_imap_code.py",
    )
    for pat in patterns:
        try:
            subprocess.run(
                ["pkill", "-TERM", "-f", pat],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    time.sleep(0.2)
    for pat in patterns:
        try:
            subprocess.run(
                ["pkill", "-KILL", "-f", pat],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def hard_exit(code: int = 130) -> None:
    """Kill children and exit immediately (do not return into the batch loop)."""
    try:
        kill_current_task()
    except Exception:
        pass
    try:
        kill_helper_processes()
    except Exception:
        pass
    try:
        clear_pid_file()
    except Exception:
        pass
    try:
        STOP_FLAG.write_text(f"stopped {time.time()}\n", encoding="utf-8")
    except Exception:
        pass
    os._exit(code)


def install_signal_handlers() -> None:
    def _handler(signum, _frame) -> None:
        name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        print(
            f"\n⏹  Signal {name} — hard-stopping batch now…",
            flush=True,
        )
        try:
            request_stop(name)
        except Exception:
            pass
        hard_exit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass


def interruptible_sleep(seconds: float) -> bool:
    """Sleep in short slices; return False if stop was requested."""
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        if stop_requested():
            return False
        time.sleep(min(0.25, end - time.time()))
    return not stop_requested()


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


def _truthy_flag(value: str | None, *, default: bool = True) -> bool:
    raw = (value or "").strip().lower()
    if raw == "":
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def wants_password_change(row: dict[str, str]) -> bool:
    """Per-row change_password (1/0); SKIP_PASSWORD_CHANGE=1 forces off for all."""
    if os.getenv("SKIP_PASSWORD_CHANGE", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return _truthy_flag(row.get("change_password"), default=True)


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
        headers = {h.strip() for h in reader.fieldnames if h}
        missing = required - headers
        if missing:
            raise SystemExit(f"CSV missing columns: {sorted(missing)}")
        for i, raw in enumerate(reader, start=2):
            row = {((k or "").strip()): (v or "").strip() for k, v in raw.items()}
            if not row.get("riot_username") or row["riot_username"].startswith("#"):
                continue
            for key in required:
                if not row.get(key):
                    raise SystemExit(f"Row {i}: missing {key}")
            # Normalize per-row flag for task file / failed.txt (1/0 only).
            row["change_password"] = (
                "1" if _truthy_flag(row.get("change_password"), default=True) else "0"
            )
            # new_password required when this row would change password (SKIP forces off).
            if wants_password_change(row) and not row.get("new_password"):
                raise SystemExit(
                    f"Row {i}: missing new_password "
                    f"(required when change_password=1; set change_password=0 to skip, "
                    f"or SKIP_PASSWORD_CHANGE=1 / --skip-password-change)"
                )
            rows.append(row)
    return rows


TASK_CSV_FIELDS = [
    "riot_username",
    "riot_password",
    "new_password",
    "imap_email",
    "imap_app_password",
    "new_email",
    "imap_host",
    "imap_port",
    "proxy_index",
    "email_change_url",
    "change_password",
]


def account_line(row: dict[str, str]) -> str:
    """Compact line written to success.txt (new_password only if password change ran)."""
    if wants_password_change(row) and row.get("new_password"):
        final_password = row["new_password"]
    else:
        final_password = row.get("riot_password", "")
    return "\t".join(
        [
            row.get("riot_username", ""),
            final_password,
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


def password_change_completed(log_path: str, output: str) -> bool:
    """True if the password step likely finished (email-first flow)."""
    blob = output or ""
    if log_path:
        try:
            blob += "\n" + Path(log_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    markers = (
        "Password change confirmed",
        "Password change verification: ok",
        "Account session ready after password change",
        "Password change submitted",
        "clicked-password-save",
    )
    return any(marker in blob for marker in markers)


def task_csv_row_for_rerun(
    row: dict[str, str], *, password_changed: bool = False
) -> dict[str, str]:
    """Build a tasks.csv-compatible row for failed.txt paste/rerun.

    With email-first flow, only swap riot_password → new_password when the
    password step actually ran (otherwise retry would use the wrong password).
    """
    out = {key: (row.get(key) or "").strip() for key in TASK_CSV_FIELDS}
    if not out.get("change_password"):
        out["change_password"] = "1" if wants_password_change(row) else "0"
    new_password = out.get("new_password") or ""
    if password_changed and new_password:
        out["riot_password"] = new_password
    out["imap_host"] = out.get("imap_host") or derive_imap_host(out.get("imap_email", ""))
    if not out.get("imap_port"):
        out["imap_port"] = "993"
    return out


def append_failed_csv(
    path: Path, row: dict[str, str], *, password_changed: bool = False
) -> None:
    """Append one paste-ready tasks.csv row (writes header if file is new/empty)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = (not path.exists()) or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TASK_CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(task_csv_row_for_rerun(row, password_changed=password_changed))


def remove_username_from_tasks_csv(path: Path, username: str) -> bool:
    """Remove a completed account from tasks.csv. Returns True if a row was removed."""
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return False
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    username = (username or "").strip()
    kept = [
        r
        for r in rows
        if ((r.get("riot_username") or "").strip() != username)
    ]
    if len(kept) == len(rows):
        return False

    # Preserve known column order; keep any extra columns from the original file.
    for key in TASK_CSV_FIELDS:
        if key not in fieldnames:
            fieldnames.append(key)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in kept:
            writer.writerow({k: (r.get(k) or "") for k in fieldnames})
    tmp.replace(path)
    return True


def run_one(
    row: dict[str, str],
    *,
    entry_url: str,
    skip_email: bool,
    skip_password: bool,
) -> tuple[bool, str, str]:
    """Returns (ok, reason, investigation_log_path)."""
    global _current_proc

    if stop_requested():
        return False, "stopped", ""

    imap_host = row.get("imap_host") or derive_imap_host(row["imap_email"])
    imap_port = row.get("imap_port") or "993"
    new_password = row.get("new_password", "")

    log_path = new_log_path(tag=row["riot_username"], prefix="safari")
    write_line(log_path, f"batch start {row['riot_username']} → {row['new_email']}")
    write_line(log_path, f"imap={row['imap_email']}@{imap_host}:{imap_port}")
    write_line(log_path, f"entry_url={entry_url}")
    do_password = (not skip_password) and wants_password_change(row) and bool(new_password)
    write_line(log_path, f"skip_email_change={int(skip_email)}")
    write_line(log_path, f"skip_password_change={int(not do_password)}")
    write_line(log_path, f"change_password={1 if do_password else 0}")
    write_line(log_path, f"password_change={int(do_password)}")

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
            "NEW_PASSWORD": new_password,
            "NEW_EMAIL": row["new_email"],
            "CHANGE_PASSWORD": "1" if do_password else "0",
            "SAFARI_BATCH": "1",
            "SAFARI_LOG_PATH": str(log_path),
        }
    )

    # Write a task file — more reliable than osascript argv / system attribute.
    task_path = ROOT / ".safari_current_task"
    task_lines = [
        f"riot_username={row['riot_username']}",
        f"riot_password={row['riot_password']}",
        f"new_password={new_password}",
        f"new_email={row['new_email']}",
        f"imap_email={row['imap_email']}",
        f"imap_app_password={row['imap_app_password']}",
        f"imap_host={imap_host}",
        f"imap_port={imap_port}",
        f"entry_url={entry_url}",
        f"skip_email_change={'1' if skip_email else '0'}",
        f"skip_password_change={'0' if do_password else '1'}",
        f"change_password={'1' if do_password else '0'}",
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

    timeout = float(os.getenv("SAFARI_TASK_TIMEOUT") or "600")
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # own process group → killpg on Stop
        )
        _current_proc = proc
        deadline = time.time() + timeout
        while True:
            if stop_requested():
                kill_current_task()
                write_line(log_path, "RESULT: stopped", level="ERROR")
                print(f"Investigation log: {log_path}", flush=True)
                return False, "stopped", str(log_path)
            if proc.poll() is not None:
                break
            if time.time() >= deadline:
                kill_current_task()
                write_line(log_path, "RESULT: timeout", level="ERROR")
                print(f"Investigation log: {log_path}", flush=True)
                return False, "timeout", str(log_path)
            time.sleep(0.2)

        out_b, err_b = proc.communicate(timeout=2)
        stdout_chunks.append(out_b or "")
        stderr_chunks.append(err_b or "")
        returncode = proc.returncode or 0
    except FileNotFoundError:
        write_line(log_path, "RESULT: osascript not found", level="ERROR")
        print(f"Investigation log: {log_path}", flush=True)
        return False, "osascript not found (macOS only)", str(log_path)
    except Exception as exc:
        kill_current_task()
        if stop_requested():
            write_line(log_path, "RESULT: stopped", level="ERROR")
            return False, "stopped", str(log_path)
        write_line(log_path, f"RESULT: error ({exc})", level="ERROR")
        return False, f"error:{type(exc).__name__}", str(log_path)
    finally:
        _current_proc = None
        try:
            task_path.unlink(missing_ok=True)
        except Exception:
            pass

    if stop_requested():
        write_line(log_path, "RESULT: stopped", level="ERROR")
        return False, "stopped", str(log_path)

    out = ("\n".join(stdout_chunks) + "\n" + "\n".join(stderr_chunks)).strip()
    if out:
        append_block(log_path, "osascript output", out)
        lines = out.splitlines()
        print("\n".join(lines[-40:]), flush=True)

    reported = extract_log_path_from_output(out)
    if reported:
        log_path = Path(reported)

    if returncode == 0 and (
        "SUCCESS" in out.upper() or "done" in "\n".join(stdout_chunks).lower()
    ):
        write_line(log_path, "RESULT: success")
        return True, "ok", str(log_path)

    # Stash for failed.txt password swap (email-first: only if password step ran).
    try:
        write_line(
            log_path,
            "password_step_done="
            + str(int(password_change_completed(str(log_path), out))),
        )
    except Exception:
        pass

    reason = "osascript_failed"
    # Prefer specific terminal errors over earlier step names that also appear in logs
    # (e.g. "password-card" / "SAVE AND VERIFY" show up even when IMAP verify fails).
    for marker in (
        "Riot login form not ready",
        "phase/form timeout",
        "No Verify Your Email link",
        "Could not restore account session",
        "Re-login failed after password change",
        "bad_creds",
        "rejected username/password",
        "hCaptcha",
        "MFA required",
        "Could not find personal-information-card__emailAddress",
        "Could not find password-card",
        "password-card__submit-btn",
        "Allow JavaScript from Apple Events",
        "Cloudflare challenge",
        "Could not find log-out-everywhere-button",
        "Could not click LOG OUT EVERYWHERE",
        "Confirm modal",
        "Timed out waiting for Riot login host",
        "SAVE AND VERIFY",
        "password-card",
        "new_password",
    ):
        if marker.lower() in out.lower():
            reason = marker.replace(" ", "_")[:80]
            break
    if returncode != 0:
        reason = f"{reason}:rc={returncode}"
    write_line(log_path, f"RESULT: failed ({reason})", level="ERROR")
    print(f"Investigation log: {log_path}", flush=True)
    return False, reason, str(log_path)


def main() -> int:
    load_dotenv()
    # Prefer our own process group so STOP_BATCH can `kill -- -$PID`.
    try:
        os.setsid()
    except Exception:
        pass
    install_signal_handlers()
    clear_stop_flag()
    write_pid_file()

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
        help="Skip email change (password change still runs unless also skipped)",
    )
    ap.add_argument(
        "--skip-password-change",
        action="store_true",
        help="Force skip password change for all rows (overrides CSV change_password=1)",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=float(os.getenv("SAFARI_BATCH_DELAY") or "25"),
        help="Base seconds between accounts (jitter added; default 25 to reduce Cloudflare)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.skip_password_change:
        os.environ["SKIP_PASSWORD_CHANGE"] = "1"

    try:
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

        # failed.txt → failed_reasons.txt (reason + log path; not for pasting)
        failed_reasons_path = failed_path.with_name(
            f"{failed_path.stem}_reasons{failed_path.suffix}"
        )

        print(f"Loaded {len(tasks)} task(s) from {csv_path}")
        print(f"  success → {success_path}")
        print(f"  failed  → {failed_path}  (CSV rows; paste back into tasks.csv)")
        print(f"  reasons → {failed_reasons_path}")
        print(f"  logs    → {ROOT / 'debug' / 'logs'}")
        print("  on success: row removed from tasks.csv")
        print("  stop    → Ctrl+C  or  double-click STOP_BATCH.command")
        if args.dry_run:
            for i, t in enumerate(tasks, 1):
                print(f"  {i}. {t['riot_username']} → {t['new_email']}")
            print("--dry-run: no Safari launched")
            return 0

        ok_n = fail_n = 0
        for i, row in enumerate(tasks, 1):
            if stop_requested():
                print(
                    f"\nBatch stopped — skipped remaining {len(tasks) - i + 1} task(s).",
                    flush=True,
                )
                break

            print(f"\n>>> TASK {i}/{len(tasks)}", flush=True)
            try:
                ok, reason, log_path = run_one(
                    row,
                    entry_url=args.entry_url,
                    skip_email=args.skip_email_change,
                    skip_password=args.skip_password_change
                    or not wants_password_change(row),
                )
            except KeyboardInterrupt:
                request_stop("KeyboardInterrupt")
                print(
                    f"\nBatch stopped during task {i} — not starting remaining accounts.",
                    flush=True,
                )
                break

            if reason == "stopped" or stop_requested():
                print(
                    f"\nBatch stopped during task {i} ({row['riot_username']}) — "
                    f"not starting remaining accounts.",
                    flush=True,
                )
                break

            line = account_line(row)
            if ok:
                append_line(success_path, line)
                removed = remove_username_from_tasks_csv(csv_path, row["riot_username"])
                ok_n += 1
                print(f"SUCCESS → {success_path.name}: {row['riot_username']}", flush=True)
                if removed:
                    print(f"  removed from {csv_path.name}", flush=True)
                else:
                    print(
                        f"  warning: could not remove {row['riot_username']} from {csv_path.name}",
                        flush=True,
                    )
                if log_path:
                    print(f"  log → {log_path}", flush=True)
            else:
                pw_done = password_change_completed(log_path or "", "")
                append_failed_csv(failed_path, row, password_changed=pw_done)
                append_line(
                    failed_reasons_path,
                    "\t".join(
                        [
                            row.get("riot_username", ""),
                            reason,
                            log_path or "",
                        ]
                    ),
                )
                fail_n += 1
                print(
                    f"FAILED  → {failed_path.name}: {row['riot_username']} ({reason})",
                    flush=True,
                )
                if pw_done:
                    print(
                        "  tip: failed.txt is CSV — copy rows into tasks.csv to rerun "
                        "(riot_password set to new_password; password step had run)",
                        flush=True,
                    )
                else:
                    print(
                        "  tip: failed.txt is CSV — copy rows into tasks.csv to rerun "
                        "(riot_password unchanged; password step had not run yet)",
                        flush=True,
                    )
                if log_path:
                    print(f"  investigation log → {log_path}", flush=True)

            if i < len(tasks) and args.delay > 0:
                pause = max(5.0, float(args.delay) + random.uniform(3.0, 12.0))
                print(f"Cool-down {pause:.0f}s before next account…", flush=True)
                if not interruptible_sleep(pause):
                    print(
                        f"\nBatch stopped during cool-down — skipped remaining "
                        f"{len(tasks) - i} task(s).",
                        flush=True,
                    )
                    break

        if stop_requested():
            print(
                f"\nBatch aborted: {ok_n} success, {fail_n} failed "
                f"(stopped before finishing all {len(tasks)})"
            )
            return 130

        print(f"\nBatch done: {ok_n} success, {fail_n} failed (of {len(tasks)})")
        print(f"Investigation logs: {ROOT / 'debug' / 'logs'}")
        return 0 if fail_n == 0 else 1
    finally:
        kill_current_task()
        clear_pid_file()
        # Leave stop flag if user created it; clear our own run marker softly.
        if _stop_requested:
            try:
                STOP_FLAG.write_text("stopped\n", encoding="utf-8")
            except Exception:
                pass
        else:
            clear_stop_flag()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted — batch stopped.", flush=True)
        hard_exit(130)
