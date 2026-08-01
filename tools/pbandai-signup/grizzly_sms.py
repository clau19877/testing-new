#!/usr/bin/env python3
"""GrizzlySMS helper for Premium Bandai (service code: bvq)."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional


DEFAULT_CONFIG = Path(__file__).with_name("config.json")
DEFAULT_BASE = "https://api.grizzlysms.com"
# https://grizzlysms.com/premium-bandai  → service code bvq
DEFAULT_SERVICE = "bvq"
# sms-activate compatible country id for USA (override in config if needed)
DEFAULT_COUNTRY = 12


def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Missing config: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def grizzly_cfg(cfg: dict) -> dict:
    return cfg.get("grizzly") or {}


def api_request(api_key: str, base_url: str, params: dict[str, Any]) -> Any:
    query = {"api_key": api_key, **{k: v for k, v in params.items() if v is not None and v != ""}}
    url = f"{base_url.rstrip('/')}/stubs/handler_api.php?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": "pbandai-signup/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GrizzlySMS HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"GrizzlySMS network error: {exc}") from exc

    if body.startswith("{") or body.startswith("["):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return body


def format_phone(phone: str, mode: str = "national", country_dial: str = "1") -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if mode == "e164":
        if phone.startswith("+"):
            return "+" + digits
        if digits.startswith(country_dial) and len(digits) > 10:
            return "+" + digits
        return f"+{country_dial}{digits}"
    if mode == "digits":
        return digits
    # national: drop leading country dial for US-style 10-digit forms
    if digits.startswith(country_dial) and len(digits) in (11, 12):
        return digits[len(country_dial) :]
    return digits


def get_balance(api_key: str, base_url: str) -> str:
    return str(api_request(api_key, base_url, {"action": "getBalance"}))


def rent_number(
    api_key: str,
    base_url: str,
    service: str,
    country: Any,
    max_price: Optional[float] = None,
) -> dict[str, str]:
    params: dict[str, Any] = {
        "action": "getNumberV2",
        "service": service,
        "country": country,
    }
    if max_price is not None:
        params["maxPrice"] = max_price

    resp = api_request(api_key, base_url, params)
    if isinstance(resp, str):
        if resp.startswith("ACCESS_NUMBER"):
            parts = resp.split(":")
            return {"activation_id": parts[1], "phone": parts[2]}
        raise SystemExit(f"GrizzlySMS rent failed: {resp}")

    activation_id = str(resp.get("activationId") or resp.get("act_id") or resp.get("id") or "")
    phone = str(resp.get("phoneNumber") or resp.get("number") or resp.get("phone") or "")
    if not activation_id or not phone:
        raise SystemExit(f"GrizzlySMS unexpected rent response: {resp}")
    return {
        "activation_id": activation_id,
        "phone": phone,
        "cost": str(resp.get("activationCost") or ""),
        "currency": str(resp.get("currency") or ""),
    }


def get_status(api_key: str, base_url: str, activation_id: str) -> dict[str, Optional[str]]:
    resp = api_request(api_key, base_url, {"action": "getStatus", "id": activation_id})
    if not isinstance(resp, str):
        return {"status": "UNKNOWN", "code": None, "raw": json.dumps(resp)}

    if resp.startswith("STATUS_OK"):
        parts = resp.split(":")
        return {"status": "OK", "code": parts[1] if len(parts) > 1 else None, "raw": resp}
    if resp.startswith("STATUS_WAIT_RETRY"):
        parts = resp.split(":")
        return {"status": "WAIT_RETRY", "code": parts[1] if len(parts) > 1 else None, "raw": resp}
    if resp.startswith("STATUS_WAIT_CODE"):
        return {"status": "WAIT_CODE", "code": None, "raw": resp}
    if resp.startswith("STATUS_WAIT_RESEND"):
        return {"status": "WAIT_RESEND", "code": None, "raw": resp}
    if resp.startswith("STATUS_CANCEL"):
        return {"status": "CANCEL", "code": None, "raw": resp}
    return {"status": "UNKNOWN", "code": None, "raw": resp}


def set_status(api_key: str, base_url: str, activation_id: str, status: int) -> str:
    return str(api_request(api_key, base_url, {"action": "setStatus", "id": activation_id, "status": status}))


def wait_for_code(
    api_key: str,
    base_url: str,
    activation_id: str,
    timeout_sec: int = 180,
    interval_sec: int = 5,
) -> str:
    # Tell provider SMS was sent / ready to receive.
    try:
        set_status(api_key, base_url, activation_id, 1)
    except SystemExit:
        pass

    started = time.time()
    while time.time() - started < timeout_sec:
        st = get_status(api_key, base_url, activation_id)
        if st["status"] == "OK" and st.get("code"):
            try:
                set_status(api_key, base_url, activation_id, 6)
            except SystemExit:
                pass
            return str(st["code"])
        if st["status"] == "CANCEL":
            raise SystemExit("GrizzlySMS activation cancelled")
        time.sleep(interval_sec)

    try:
        set_status(api_key, base_url, activation_id, 8)
    except SystemExit:
        pass
    raise SystemExit(f"Timed out waiting for GrizzlySMS code (activation {activation_id})")


def cmd_balance(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    g = grizzly_cfg(cfg)
    print(get_balance(g["api_key"], g.get("base_url", DEFAULT_BASE)))
    return 0


def cmd_rent(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    g = grizzly_cfg(cfg)
    result = rent_number(
        g["api_key"],
        g.get("base_url", DEFAULT_BASE),
        args.service or g.get("service", DEFAULT_SERVICE),
        args.country if args.country is not None else g.get("country", DEFAULT_COUNTRY),
        args.max_price if args.max_price is not None else g.get("max_price"),
    )
    mode = g.get("phone_format", "national")
    dial = str(g.get("country_dial", "1"))
    result["phone_form"] = format_phone(result["phone"], mode=mode, country_dial=dial)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    g = grizzly_cfg(cfg)
    code = wait_for_code(
        g["api_key"],
        g.get("base_url", DEFAULT_BASE),
        args.id,
        timeout_sec=int(args.timeout or g.get("timeout_sec", 180)),
        interval_sec=int(args.interval or g.get("interval_sec", 5)),
    )
    print(code)
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    g = grizzly_cfg(cfg)
    print(set_status(g["api_key"], g.get("base_url", DEFAULT_BASE), args.id, 8))
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    g = grizzly_cfg(cfg)
    print(set_status(g["api_key"], g.get("base_url", DEFAULT_BASE), args.id, 6))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GrizzlySMS client for Premium Bandai (bvq)")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("balance")
    sp.set_defaults(func=cmd_balance)

    sp = sub.add_parser("rent")
    sp.add_argument("--service", default=None)
    sp.add_argument("--country", default=None)
    sp.add_argument("--max-price", type=float, default=None)
    sp.set_defaults(func=cmd_rent)

    sp = sub.add_parser("wait")
    sp.add_argument("--id", required=True)
    sp.add_argument("--timeout", type=int, default=None)
    sp.add_argument("--interval", type=int, default=None)
    sp.set_defaults(func=cmd_wait)

    sp = sub.add_parser("cancel")
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_cancel)

    sp = sub.add_parser("complete")
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_complete)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
