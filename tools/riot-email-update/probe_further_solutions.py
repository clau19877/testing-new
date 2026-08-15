#!/usr/bin/env python3
"""
Probe further enterprise solutions beyond YesCaptcha/2Captcha vision.

Prioritized with existing keys:
  1. Capless (explicitly lists Riot sitekey) + UA sync from solver response
  2. Multibot token (if MULTIBOT_API_KEY)
  3. NopeCHA token (if NOPECHA_API_KEY)

Also documents matrix in debug/further_solutions_report.json

Usage:
  PROXY_INDEX=35 .venv/bin/python probe_further_solutions.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from captcha import CaptchaSolverError, solve_capless_full  # noqa: E402
from captcha_providers import proxy_egress_ip, solve_with_provider  # noqa: E402
from proxyutil import load_proxy_list, to_http_url  # noqa: E402
from riot_api_login import (  # noqa: E402
    AUTH_BASE,
    LOGIN_API,
    bootstrap_login,
    extract_captcha,
    resolve_ua,
    _session,
)


def pick_proxy() -> str:
    idx = int(os.getenv("PROXY_INDEX") or "0")
    proxies = load_proxy_list(os.getenv("PROXY_LIST") or "data/proxies.txt")
    if proxies:
        return proxies[idx % len(proxies)]
    raise SystemExit("No proxy configured")


def providers() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    mapping = [
        ("capless", os.getenv("CAPLESS_API_KEY")),
        ("multibot", os.getenv("MULTIBOT_API_KEY") or os.getenv("MULTIBOT_KEY")),
        ("nopecha", os.getenv("NOPECHA_API_KEY") or os.getenv("NOPECHA_KEY")),
    ]
    wanted = [
        p.strip().lower()
        for p in (os.getenv("FURTHER_PROVIDERS") or "capless,multibot,nopecha").split(",")
        if p.strip()
    ]
    for name in wanted:
        key = ""
        for n, k in mapping:
            if n == name and k and k.strip():
                key = k.strip()
                break
        if key:
            out.append((name, key))
        else:
            print(f"[skip] {name}: no API key", flush=True)
    return out


def fetch_challenge(proxy: str, user: str, password: str, ua: str):
    session = _session(proxy, user_agent=ua)
    data = bootstrap_login(session)
    sitekey, rqdata = extract_captcha(data)
    if not sitekey or not rqdata:
        r = session.put(
            LOGIN_API,
            json={
                "type": "auth",
                "remember": False,
                "language": "en_US",
                "riot_identity": {"username": user, "password": password},
            },
            timeout=60,
        )
        data = r.json()
        sitekey, rqdata = extract_captcha(data)
    if not sitekey or not rqdata:
        raise RuntimeError(f"No sitekey/rqdata: {json.dumps(data)[:400]}")
    return sitekey, rqdata, session


def submit(session, user: str, password: str, token: str) -> dict:
    r = session.put(
        LOGIN_API,
        json={
            "type": "auth",
            "remember": False,
            "language": "en_US",
            "riot_identity": {
                "username": user,
                "password": password,
                "captcha": f"hcaptcha {token}",
            },
        },
        timeout=60,
    )
    try:
        body = r.json()
    except Exception:
        return {"http": r.status_code, "raw": r.text[:400]}
    return {"http": r.status_code, **body}


def main() -> int:
    user = os.getenv("RIOT_USERNAME") or ""
    password = os.getenv("RIOT_PASSWORD") or ""
    if not user or not password:
        print("Missing RIOT_USERNAME / RIOT_PASSWORD", flush=True)
        return 1

    proxy = pick_proxy()
    egress = proxy_egress_ip(proxy)
    ua = resolve_ua(os.getenv("RIOT_UA_MODE") or "chrome")
    print(f"proxy={to_http_url(proxy).split('@')[-1]} egress={egress}", flush=True)
    print(f"ua={ua[:60]}…", flush=True)

    report: list[dict] = []
    for name, key in providers():
        row: dict = {"provider": name, "egress_ip": egress}
        print(f"\n=== {name} ===", flush=True)
        try:
            sitekey, rqdata, session = fetch_challenge(proxy, user, password, ua)
            row["sitekey"] = sitekey
            row["rqdata_len"] = len(rqdata)
            print(f"  challenge rqdata_len={len(rqdata)}", flush=True)
            t0 = time.time()
            website_url = f"{AUTH_BASE}/"
            try:
                if name == "capless":
                    token, ret_ua = solve_capless_full(
                        key,
                        website_url=website_url,
                        website_key=sitekey,
                        proxy=proxy,
                        rqdata=rqdata,
                        timeout=float(os.getenv("CAPTCHA_TIMEOUT") or "180"),
                    )
                    if ret_ua:
                        session.headers["User-Agent"] = ret_ua
                        row["solver_ua"] = ret_ua
                        print(f"  Capless UA synced → {ret_ua[:56]}…", flush=True)
                else:
                    token = solve_with_provider(
                        name,
                        key,
                        website_url=website_url,
                        website_key=sitekey,
                        rqdata=rqdata,
                        user_agent=ua,
                        proxy=proxy,
                        timeout=float(os.getenv("CAPTCHA_TIMEOUT") or "180"),
                        require_proxy=True,
                        require_rqdata=True,
                    )
                row["solve"] = "ok"
                row["token_prefix"] = token[:12]
                row["token_len"] = len(token)
                row["solve_s"] = round(time.time() - t0, 1)
                print(f"  token ok {token[:12]}… ({row['solve_s']}s)", flush=True)
            except CaptchaSolverError as exc:
                row["solve"] = "fail"
                row["solve_error"] = str(exc)[:400]
                row["solve_s"] = round(time.time() - t0, 1)
                print(f"  SOLVE FAIL: {exc}", flush=True)
                report.append(row)
                continue

            result = submit(session, user, password, token)
            row["riot_type"] = result.get("type")
            row["riot_error"] = result.get("error")
            row["riot_http"] = result.get("http")
            if result.get("type") in ("success", "multifactor", "login") or (
                result.get("success") or {}
            ).get("login_token"):
                row["riot_accept"] = True
            else:
                row["riot_accept"] = False
                row["riot_new_captcha"] = bool(result.get("captcha"))
            print(
                f"  Riot accept={row.get('riot_accept')} type={row.get('riot_type')} "
                f"error={row.get('riot_error')}",
                flush=True,
            )
        except Exception as exc:
            row["fatal"] = str(exc)[:400]
            print(f"  FATAL: {exc}", flush=True)
        report.append(row)

    out = ROOT / "debug" / "further_solutions_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nWrote {out}", flush=True)
    print(json.dumps(report, indent=2), flush=True)
    return 0 if any(r.get("riot_accept") for r in report) else 2


if __name__ == "__main__":
    raise SystemExit(main())
