#!/usr/bin/env python3
"""Batch Riot email updates via macOS Safari AppleScript.

Reads accounts from a CSV, runs safari_riot_email.applescript one-by-one,
appends successes to success.txt and failures to failed.txt.

Stop immediately (does not continue remaining tasks):
  - Ctrl+C / Terminal Stop
  - double-click STOP_BATCH.command
  - touch .safari_batch_stop in the toolkit folder

CSV columns (same as data/tasks.csv.example):
  riot_username,riot_password,new_password,imap_email,imap_app_password,new_email,
  imap_host,imap_port,proxy_index,email_change_url

Flow per account: login → change password → change email → verify → logout.

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
            proc.wait(timeout=2)
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


def install_signal_handlers() -> None:
    def _handler(signum, _frame) -> None:
        name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        request_stop(name)

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
        skip_pw = os.getenv("SKIP_PASSWORD_CHANGE", "").strip() in {"1", "true", "yes"}
        for i, raw in enumerate(reader, start=2):
            row = {((k or "").strip()): (v or "").strip() for k, v in raw.items()}
            if not row.get("riot_username") or row["riot_username"].startswith("#"):
                continue
            for key in required:
                if not row.get(key):
                    raise SystemExit(f"Row {i}: missing {key}")
            if not skip_pw and not row.get("new_password"):
                raise SystemExit(
                    f"Row {i}: missing new_password "
                    f"(add column, or SKIP_PASSWORD_CHANGE=1 / --skip-password-change)"
                )
            rows.append(row)
    return rows


def account_line(row: dict[str, str]) -> str:
    """Compact line written to success.txt / failed.txt (uses new_password when set)."""
    final_password = row.get("new_password") or row.get("riot_password", "")
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
    write_line(log_path, f"skip_email_change={int(skip_email)}")
    write_line(log_path, f"skip_password_change={int(skip_password)}")
    write_line(log_path, f"password_change={int(bool(new_password) and not skip_password)}")

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
        f"skip_password_change={'1' if skip_password else '0'}",
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

    reason = "osascript_failed"
    # Prefer specific terminal errors over earlier step names that also appear in logs
    # (e.g. "password-card" / "SAVE AND VERIFY" show up even when IMAP verify fails).
    for marker in (
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
        "Allow JavaScript",
        "Cloudflare",
        "cloudflare",
        "log-out-everywhere-button",
        "LOG OUT EVERYWHERE",
        "modal_close-btn",
        "Confirm modal",
        "Timed out",
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
        help="Skip password change step (login → email only)",
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

        print(f"Loaded {len(tasks)} task(s) from {csv_path}")
        print(f"  success → {success_path}")
        print(f"  failed  → {failed_path}")
        print(f"  logs    → {ROOT / 'debug' / 'logs'}")
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
                    skip_password=args.skip_password_change,
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
        request_stop("KeyboardInterrupt")
        kill_current_task()
        clear_pid_file()
        print("\nInterrupted — batch stopped.", flush=True)
        raise SystemExit(130)
