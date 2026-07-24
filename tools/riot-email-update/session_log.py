"""
Session file logger — records everything printed during a run for later enhancement.

Log file: debug/logs/session_YYYYMMDD_HHMMSS_<tag>.log

Usage:
  from session_log import start_session_log, log, get_log_path
  start_session_log(tag="admin10025746")
  log("step", "opened login page")
  # all print() output is also tee'd into the same file
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "debug" / "logs"

_lock = threading.Lock()
_fp: TextIO | None = None
_path: Path | None = None
_orig_stdout: TextIO | None = None
_orig_stderr: TextIO | None = None
_installed = False


class _Tee:
    def __init__(self, primary: TextIO, secondary: TextIO) -> None:
        self.primary = primary
        self.secondary = secondary

    def write(self, data: str) -> int:
        try:
            self.primary.write(data)
            self.primary.flush()
        except Exception:
            pass
        try:
            with _lock:
                if self.secondary and not self.secondary.closed:
                    self.secondary.write(data)
                    self.secondary.flush()
        except Exception:
            pass
        return len(data) if data else 0

    def flush(self) -> None:
        try:
            self.primary.flush()
        except Exception:
            pass
        try:
            with _lock:
                if self.secondary and not self.secondary.closed:
                    self.secondary.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self.primary.isatty())
        except Exception:
            return False

    @property
    def encoding(self) -> str:
        return getattr(self.primary, "encoding", None) or "utf-8"


def get_log_path() -> Path | None:
    return _path


def start_session_log(*, tag: str = "", log_dir: Path | None = None) -> Path:
    """Open a new session log file and tee stdout/stderr into it."""
    global _fp, _path, _orig_stdout, _orig_stderr, _installed

    directory = Path(log_dir) if log_dir else LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (tag or "run"))[:48]
    path = directory / f"session_{stamp}_{safe or 'run'}.log"

    # Close previous session if any
    stop_session_log()

    _fp = path.open("a", encoding="utf-8", newline="\n")
    _path = path
    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr
    sys.stdout = _Tee(_orig_stdout, _fp)  # type: ignore[assignment]
    sys.stderr = _Tee(_orig_stderr, _fp)  # type: ignore[assignment]
    _installed = True

    log("session", f"log file → {path}")
    log("session", f"cwd={os.getcwd()}")
    log("session", f"python={sys.executable}")
    log("session", f"argv={sys.argv!r}")
    return path


def stop_session_log() -> None:
    global _fp, _path, _orig_stdout, _orig_stderr, _installed
    if _installed and _orig_stdout is not None:
        sys.stdout = _orig_stdout
    if _installed and _orig_stderr is not None:
        sys.stderr = _orig_stderr
    _installed = False
    _orig_stdout = None
    _orig_stderr = None
    if _fp is not None:
        try:
            _fp.flush()
            _fp.close()
        except Exception:
            pass
    _fp = None
    # keep _path so callers can still report it after stop


def log(topic: str, message: str, *args: Any, level: str = "INFO") -> None:
    """Structured log line (also goes through tee'd stdout)."""
    try:
        body = message % args if args else message
    except Exception:
        body = f"{message} {args}"
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level}] [{topic}] {body}"
    print(line, flush=True)


def log_exception(topic: str, exc: BaseException) -> None:
    log(topic, f"{type(exc).__name__}: {exc}", level="ERROR")
    print(traceback.format_exc(), flush=True)


def attach_page_logging(page, *, topic: str = "page") -> None:
    """Record navigations, consoles, and page errors on a Playwright page."""
    try:
        page.on(
            "framenavigated",
            lambda frame: log(topic, f"nav → {frame.url}") if frame == page.main_frame else None,
        )
    except Exception:
        pass
    try:
        page.on(
            "console",
            lambda msg: log(topic, f"console.{msg.type}: {msg.text}"),
        )
    except Exception:
        pass
    try:
        page.on(
            "pageerror",
            lambda err: log(topic, f"pageerror: {err}", level="ERROR"),
        )
    except Exception:
        pass
    try:
        page.on(
            "requestfailed",
            lambda req: log(
                topic,
                f"requestfailed {req.method} {req.url} → {req.failure}",
                level="WARN",
            ),
        )
    except Exception:
        pass
