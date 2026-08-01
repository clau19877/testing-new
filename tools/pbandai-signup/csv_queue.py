#!/usr/bin/env python3
"""Manage Premium Bandai signup CSV queues: task.csv → success.csv / failed.csv."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import random_data
import signup_log


TASK_FIELDS = [
    "email",
    "password",
    "first_name",
    "last_name",
    "gender",
    "month",
    "day",
    "year",
    "phone",
    "phone_country_iso",
    "address1",
    "address2",
    "city",
    "state",
    "zip",
    "country",
]

SUCCESS_FIELDS = TASK_FIELDS + ["activation_id", "created_at", "note"]
FAILED_FIELDS = TASK_FIELDS + ["activation_id", "failed_at", "reason"]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_csv(path: Path, fieldnames: list[str]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return TASK_FIELDS, []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or TASK_FIELDS)
        rows = []
        for raw in reader:
            row = {k: (raw.get(k) or "").strip() for k in fieldnames}
            if not any(row.values()):
                continue
            rows.append(row)
        return fieldnames, rows


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    tmp.replace(path)


def append_row(path: Path, fieldnames: list[str], row: dict[str, str]) -> None:
    ensure_csv(path, fieldnames)
    _, existing = read_rows(path)
    # Re-read header from file for stability if custom columns exist.
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or fieldnames)
    existing.append({k: row.get(k, "") for k in header})
    write_rows(path, header, existing)


def normalize_task_row(row: dict[str, str]) -> dict[str, str]:
    out = {k: (row.get(k) or "").strip() for k in TASK_FIELDS}
    # Allow "Email"/"Password" style headers via case-insensitive map.
    lower_map = {k.lower(): (v or "").strip() for k, v in row.items() if k}
    for key in TASK_FIELDS:
        if not out[key] and key in lower_map:
            out[key] = lower_map[key]
    return out


def load_config_if_present(base_dir: Path) -> dict:
    cfg_path = Path(base_dir) / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _is_random(value: str) -> bool:
    return (value or "").strip().lower() == "random"


def resolve_random_row(row: dict[str, str], cfg: Optional[dict] = None) -> dict[str, str]:
    """Replace any "random" cell with a generated value from random_data's pools."""
    resolved = dict(row)
    cfg = cfg or {}

    # Date of birth: resolve month/day/year together so "day" respects
    # whichever month ends up being used (fixed or freshly randomized).
    if _is_random(resolved.get("month")) or _is_random(resolved.get("day")) or _is_random(resolved.get("year")):
        month = random_data.random_month() if _is_random(resolved.get("month")) else resolved.get("month")
        year = random_data.random_year() if _is_random(resolved.get("year")) else resolved.get("year")
        day = random_data.random_day(month) if _is_random(resolved.get("day")) else resolved.get("day")
        resolved["month"], resolved["day"], resolved["year"] = month, day, year

    # City/state/zip: pick one consistent tuple if 2+ of them are "random".
    csz_keys = ["city", "state", "zip"]
    csz_random = [k for k in csz_keys if _is_random(resolved.get(k))]
    if len(csz_random) >= 2:
        city, state, zip_code = random_data.random_city_state_zip()
        values = {"city": city, "state": state, "zip": zip_code}
        for k in csz_random:
            resolved[k] = values[k]
    for k in csz_keys:
        if _is_random(resolved.get(k)):
            city, state, zip_code = random_data.random_city_state_zip()
            resolved[k] = {"city": city, "state": state, "zip": zip_code}[k]

    simple_generators = {
        "first_name": random_data.random_first_name,
        "last_name": random_data.random_last_name,
        "gender": random_data.random_gender,
        "password": random_data.random_password,
        "country": random_data.random_country,
        "address1": random_data.random_street_address,
        "address2": lambda: "",
    }
    for field, generator in simple_generators.items():
        if _is_random(resolved.get(field)):
            resolved[field] = generator()

    if _is_random(resolved.get("phone")):
        # A fake number can't receive a real SMS. Clear it so GrizzlySMS (if
        # enabled) rents a real number, matching the documented "leave phone
        # empty" convention.
        resolved["phone"] = ""

    if _is_random(resolved.get("phone_country_iso")):
        # No sensible "random" value here — clear it so the auto-detected
        # value (from GrizzlySMS's rented number, or the Area code) is used.
        resolved["phone_country_iso"] = ""

    if _is_random(resolved.get("email")):
        template = (cfg.get("random_data") or {}).get("email_template", "")
        if not template:
            raise ValueError(
                "email is 'random' but random_data.email_template is not set in config.json"
            )
        resolved["email"] = template.replace("{token}", random_data.random_token())

    return resolved


def cmd_init(args: argparse.Namespace) -> int:
    ensure_csv(args.task, TASK_FIELDS)
    ensure_csv(args.success, SUCCESS_FIELDS)
    ensure_csv(args.failed, FAILED_FIELDS)
    print(f"Ready: {args.task.name}, {args.success.name}, {args.failed.name}")
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    _, rows = read_rows(args.task)
    print(len(rows))
    return 0


def _masked(row: dict[str, str]) -> dict[str, str]:
    masked = dict(row)
    if masked.get("password"):
        masked["password"] = f"[REDACTED, {len(masked['password'])} chars]"
    return masked


def cmd_next(args: argparse.Namespace) -> int:
    ensure_csv(args.task, TASK_FIELDS)
    fieldnames, rows = read_rows(args.task)
    signup_log.log_debug(f"next: {len(rows)} row(s) in task.csv", source="csv_queue", step="next")
    if not rows:
        print("{}")
        return 0
    row = normalize_task_row(rows[0])
    signup_log.log_debug(
        f"next: raw first row = {_masked(row)}",
        source="csv_queue",
        step="next",
        email=row.get("email", ""),
    )

    cfg = load_config_if_present(args.dir)
    try:
        resolved = resolve_random_row(row, cfg)
    except ValueError as exc:
        signup_log.log_error(str(exc), source="csv_queue", step="next_resolve", email=row.get("email", ""))
        print(json.dumps({"error": str(exc), "email": row.get("email", "")}, ensure_ascii=False))
        return 2

    if resolved != row:
        signup_log.log_debug(
            f"next: resolved 'random' fields -> {_masked(resolved)}",
            source="csv_queue",
            step="next_resolve",
            email=resolved.get("email", ""),
        )

    if not resolved.get("email") or not resolved.get("password"):
        print(
            json.dumps(
                {
                    "error": "first task.csv row is missing email or password",
                    "email": resolved.get("email", ""),
                },
                ensure_ascii=False,
            )
        )
        return 2

    if resolved != row:
        # Persist resolved values so success.csv/failed.csv record what was
        # actually submitted instead of the literal word "random".
        rest = [normalize_task_row(r) for r in rows[1:]]
        write_rows(args.task, TASK_FIELDS, [resolved] + rest)

    print(json.dumps(resolved, ensure_ascii=False))
    return 0


def remove_email(task_path: Path, email: str) -> Optional[dict[str, str]]:
    _fieldnames, rows = read_rows(task_path)
    email_l = email.strip().lower()
    kept: list[dict[str, str]] = []
    removed: Optional[dict[str, str]] = None
    for row in rows:
        norm = normalize_task_row(row)
        if removed is None and norm["email"].lower() == email_l:
            removed = norm
            continue
        kept.append(norm)
    if removed is None:
        return None
    write_rows(task_path, TASK_FIELDS, kept)
    return removed


def cmd_success(args: argparse.Namespace) -> int:
    ensure_csv(args.task, TASK_FIELDS)
    ensure_csv(args.success, SUCCESS_FIELDS)
    signup_log.log_debug(
        f"success: email={args.email} note={args.note!r} phone={args.phone!r} activation_id={args.activation_id!r}",
        source="csv_queue",
        step="success",
        email=args.email,
        phone=args.phone,
        activation_id=args.activation_id,
    )
    removed = remove_email(args.task, args.email)
    if removed is None:
        signup_log.log_error(
            f"success called but email not found in task.csv: {args.email}",
            source="csv_queue",
            step="success",
            email=args.email,
        )
        print(f"email not found in task.csv: {args.email}", file=sys.stderr)
        return 1
    payload = dict(removed)
    if args.phone:
        payload["phone"] = args.phone
    if args.activation_id:
        payload["activation_id"] = args.activation_id
    else:
        payload.setdefault("activation_id", "")
    payload["created_at"] = utc_now()
    payload["note"] = args.note or "created"
    append_row(args.success, SUCCESS_FIELDS, payload)
    print(json.dumps({"status": "success", "email": removed["email"]}, ensure_ascii=False))
    return 0


def cmd_failed(args: argparse.Namespace) -> int:
    ensure_csv(args.task, TASK_FIELDS)
    ensure_csv(args.failed, FAILED_FIELDS)
    signup_log.log_debug(
        f"failed: email={args.email} reason={args.reason!r} phone={args.phone!r} activation_id={args.activation_id!r}",
        source="csv_queue",
        step="failed",
        email=args.email,
        phone=args.phone,
        activation_id=args.activation_id,
    )
    removed = remove_email(args.task, args.email)
    if removed is None:
        # Still record failure even if already removed.
        removed = {"email": args.email, "password": args.password or ""}
        for key in TASK_FIELDS:
            removed.setdefault(key, "")
    payload = dict(removed)
    if args.phone:
        payload["phone"] = args.phone
    if args.activation_id:
        payload["activation_id"] = args.activation_id
    else:
        payload.setdefault("activation_id", "")
    payload["failed_at"] = utc_now()
    payload["reason"] = args.reason or "unknown"
    append_row(args.failed, FAILED_FIELDS, payload)
    signup_log.log_error(
        payload["reason"],
        source="csv_queue",
        step="mark_failed",
        email=payload.get("email", ""),
        phone=payload.get("phone", ""),
        activation_id=payload.get("activation_id", ""),
        extra={"password_set": bool(payload.get("password"))},
    )
    print(json.dumps({"status": "failed", "email": payload["email"], "reason": payload["reason"]}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CSV queue helpers for Premium Bandai signup")
    p.add_argument("--dir", type=Path, default=Path(__file__).resolve().parent)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in (
        ("init", cmd_init),
        ("count", cmd_count),
        ("next", cmd_next),
    ):
        sp = sub.add_parser(name)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("success")
    sp.add_argument("--email", required=True)
    sp.add_argument("--note", default="created")
    sp.add_argument("--phone", default="")
    sp.add_argument("--activation-id", default="")
    sp.set_defaults(func=cmd_success)

    sp = sub.add_parser("failed")
    sp.add_argument("--email", required=True)
    sp.add_argument("--password", default="")
    sp.add_argument("--reason", default="unknown")
    sp.add_argument("--phone", default="")
    sp.add_argument("--activation-id", default="")
    sp.set_defaults(func=cmd_failed)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base: Path = args.dir
    args.task = base / "task.csv"
    args.success = base / "success.csv"
    args.failed = base / "failed.csv"
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
