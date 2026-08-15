#!/usr/bin/env python3
"""Investigation logger for Safari / batch error diagnosis.

Writes timestamped lines under debug/logs/ so a failed account run can be
shared and inspected later (steps, URL/title probes, osascript output).

Usage:
  from investigate_log import new_log_path, write_line, append_block
  path = new_log_path(tag="RiotUser", prefix="safari")
  write_line(path, "starting account")
  append_block(path, "osascript stderr", "...")
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "debug" / "logs"

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def log_dir(path: Path | None = None) -> Path:
    directory = Path(path) if path else LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_tag(tag: str, *, max_len: int = 48) -> str:
    cleaned = _SAFE.sub("_", (tag or "run").strip())[:max_len]
    return cleaned or "run"


def new_log_path(
    *,
    tag: str = "",
    prefix: str = "safari",
    log_directory: Path | None = None,
) -> Path:
    """Return a fresh path like debug/logs/safari_YYYYMMDD_HHMMSS_<tag>.log."""
    directory = log_dir(log_directory)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return directory / f"{prefix}_{stamp}_{safe_tag(tag)}.log"


def write_line(
    path: Path,
    message: str,
    *,
    topic: str = "investigate",
    level: str = "INFO",
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level}] [{topic}] {message}"
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def append_block(path: Path, title: str, body: str) -> None:
    """Append a titled multi-line block (e.g. captured osascript output)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    text = (body or "").rstrip("\n")
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"[{ts}] --- {title} ---\n")
        if text:
            f.write(text + "\n")
        f.write(f"[{ts}] --- end {title} ---\n")


def extract_log_path_from_output(text: str) -> str:
    """Parse 'Debug log: /path' or 'log file → /path' from AppleScript output."""
    if not text:
        return ""
    for line in reversed(text.splitlines()):
        low = line.lower()
        for marker in ("debug log:", "log file →", "log file ->"):
            idx = low.rfind(marker)
            if idx >= 0:
                # Use original line for path casing
                orig = line[idx + len(marker) :].strip()
                if orig:
                    return orig
    # Env fallback when AppleScript reused SAFARI_LOG_PATH
    return (os.environ.get("SAFARI_LOG_PATH") or "").strip()
