#!/usr/bin/env python3
"""Structured logging for Premium Bandai signup helper.

Writes:
  - logs/errors.jsonl  (errors only, one JSON object per line)
  - logs/signup.log    (human-readable INFO/WARNING/ERROR trail)
  - logs/trace.log     (everything, including DEBUG-level in/out detail:
                         every shell command, every Safari JS call and its
                         result, every button/field match attempt, every
                         IMAP/GrizzlySMS API call and response)
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_DIR = TOOL_DIR / "logs"
ERROR_JSONL = "errors.jsonl"
SIGNUP_LOG = "signup.log"
TRACE_LOG = "trace.log"

# Command-line flags whose values must never be written to any log file.
SENSITIVE_FLAGS = (
    "--password",
    "--app-specific-password",
    "--app_specific_password",
    "--api-key",
    "--api_key",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ensure_log_dir(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _append_text(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        if not line.endswith("\n"):
            fh.write("\n")


def redact(text: str) -> str:
    """Best-effort redaction of secret values from a shell command / message
    before it's written to any log file."""
    if not text:
        return text
    tokens = text.split(" ")
    out = []
    redact_next = False
    for tok in tokens:
        if redact_next:
            out.append("[REDACTED]")
            redact_next = False
            continue
        out.append(tok)
        if tok in SENSITIVE_FLAGS:
            redact_next = True
    return " ".join(out)


def truncate(text: str, limit: int = 2000) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text)} chars total]"


def log_record(
    level: str,
    message: str,
    *,
    log_dir: Optional[Path] = None,
    source: str = "",
    step: str = "",
    email: str = "",
    phone: str = "",
    activation_id: str = "",
    extra: Optional[dict[str, Any]] = None,
    exc: Optional[BaseException] = None,
) -> dict[str, Any]:
    """Append a structured log record. Errors also go to errors.jsonl."""
    base = ensure_log_dir(log_dir or DEFAULT_LOG_DIR)
    record: dict[str, Any] = {
        "ts": utc_now(),
        "level": level.upper(),
        "message": message,
        "source": source,
        "step": step,
        "email": email,
        "phone": phone,
        "activation_id": activation_id,
    }
    if extra:
        record["extra"] = extra
    if exc is not None:
        record["exception_type"] = type(exc).__name__
        record["exception"] = str(exc)
        record["traceback"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    # Human-readable line
    bits = [record["ts"], record["level"]]
    if step:
        bits.append(f"step={step}")
    if email:
        bits.append(f"email={email}")
    if source:
        bits.append(f"source={source}")
    bits.append(message)
    line = " | ".join(bits)

    # trace.log gets everything (including DEBUG in/out detail).
    _append_text(base / TRACE_LOG, line)
    # signup.log stays focused on notable events (no DEBUG noise).
    if record["level"] != "DEBUG":
        _append_text(base / SIGNUP_LOG, line)

    if record["level"] in {"ERROR", "CRITICAL"}:
        _append_text(base / ERROR_JSONL, json.dumps(record, ensure_ascii=False))

    return record


def log_error(message: str, **kwargs: Any) -> dict[str, Any]:
    return log_record("ERROR", message, **kwargs)


def log_info(message: str, **kwargs: Any) -> dict[str, Any]:
    return log_record("INFO", message, **kwargs)


def log_debug(message: str, **kwargs: Any) -> dict[str, Any]:
    return log_record("DEBUG", message, **kwargs)


def log_io(
    direction: str,
    message: str,
    *,
    redact_message: bool = False,
    truncate_at: int = 2000,
    **kwargs: Any,
) -> dict[str, Any]:
    """Convenience for logging an OUT (request/command/JS sent) or IN
    (response/result received) event at DEBUG level, safely redacted and
    truncated."""
    safe = redact(message) if redact_message else message
    safe = truncate(safe, truncate_at)
    return log_debug(f"{direction} {safe}", **kwargs)


def cmd_write(args: argparse.Namespace) -> int:
    extra = None
    if args.extra_json:
        try:
            extra = json.loads(args.extra_json)
        except json.JSONDecodeError as exc:
            print(f"invalid --extra-json: {exc}", file=sys.stderr)
            return 2

    record = log_record(
        args.level,
        args.message,
        log_dir=Path(args.log_dir) if args.log_dir else DEFAULT_LOG_DIR,
        source=args.source or "",
        step=args.step or "",
        email=args.email or "",
        phone=args.phone or "",
        activation_id=args.activation_id or "",
        extra=extra,
    )
    print(json.dumps({"logged": True, "level": record["level"], "ts": record["ts"]}, ensure_ascii=False))
    return 0


def cmd_tail_errors(args: argparse.Namespace) -> int:
    path = (Path(args.log_dir) if args.log_dir else DEFAULT_LOG_DIR) / ERROR_JSONL
    if not path.exists():
        print("[]")
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    keep = lines[-args.n :] if args.n > 0 else lines
    print("[")
    for i, line in enumerate(keep):
        suffix = "," if i < len(keep) - 1 else ""
        print(f"  {line}{suffix}")
    print("]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Signup helper logger")
    p.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("write")
    sp.add_argument("--level", default="ERROR", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    sp.add_argument("--message", required=True)
    sp.add_argument("--source", default="")
    sp.add_argument("--step", default="")
    sp.add_argument("--email", default="")
    sp.add_argument("--phone", default="")
    sp.add_argument("--activation-id", default="")
    sp.add_argument("--extra-json", default="")
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("tail-errors")
    sp.add_argument("-n", type=int, default=20)
    sp.set_defaults(func=cmd_tail_errors)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
