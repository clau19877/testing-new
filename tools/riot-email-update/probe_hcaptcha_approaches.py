#!/usr/bin/env python3
"""
Probe alternate hCaptcha approaches against live Riot authenticate API.

Flow (matches open-source Valorant/Riot auth):
  1. Same residential proxy for challenge + submit
  2. PUT /api/v1/login → extract sitekey + rqdata
  3. Ask each configured solver for a token bound to that rqdata
  4. PUT /api/v1/login with captcha token → see if Riot accepts it

Configured via .env keys (any subset):
  CAPLESS_API_KEY, CAPSOLVER_API_KEY, CAPMONSTER_API_KEY,
  TWOCAPTCHA_API_KEY / TWO_CAPTCHA_API_KEY, NONECAP_API_KEY

Usage:
  cd tools/riot-email-update
  .venv/bin/python probe_hcaptcha_approaches.py
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

from captcha import CaptchaSolverError  # noqa: E402
from captcha_providers import DEFAULT_UA, solve_with_provider  # noqa: E402
from proxyutil import load_proxy_list, to_http_url  # noqa: E402
from riot_api_login import (  # noqa: E402
    AUTH_BASE,
    LOGIN_API,
    bootstrap_login,
    extract_captcha,
    _session,
)


def pick_proxy() -> str:
    idx = int(os.getenv("PROXY_INDEX") or "0")
    proxies = load_proxy_list(os.getenv("PROXY_LIST") or "data/proxies.txt")
    if proxies:
        return proxies[idx % len(proxies)]
    raw = (
        os.getenv("CAPLESS_PROXY")
        or os.getenv("BROWSER_PROXY")
        or os.getenv("CAPTCHA_PROXY")
        or ""
    )
    if not raw:
        raise SystemExit("No proxy configured (PROXY_LIST / CAPLESS_PROXY)")
    return raw


def configured_providers() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    mapping = [
        ("capless", os.getenv("CAPLESS_API_KEY")),
        ("capsolver", os.getenv("CAPSOLVER_API_KEY")),
        ("capmonster", os.getenv("CAPMONSTER_API_KEY") or os.getenv("CAP_MONSTER_API_KEY")),
        (
            "twocaptcha",
            os.getenv("TWOCAPTCHA_API_KEY")
            or os.getenv("TWO_CAPTCHA_API_KEY")
            or os.getenv("2CAPTCHA_API_KEY"),
        ),
        ("nonecap", os.getenv("NONECAP_API_KEY")),
    ]
    for name, key in mapping:
        if key and key.strip():
            out.append((name, key.strip()))
    return out


def fetch_challenge(proxy: str, username: str, password: str) -> tuple[str, str, object]:
    session = _session(proxy, riot_client=True)
    data = bootstrap_login(session)
    sitekey, rqdata = extract_captcha(data)
    if not sitekey or not rqdata:
        # Fallback: PUT credentials to force a new captcha blob
        r = session.put(
            LOGIN_API,
            json={
                "type": "auth",
                "remember": False,
                "language": "en_US",
                "riot_identity": {"username": username, "password": password},
            },
            timeout=60,
        )
        data = r.json()
        sitekey, rqdata = extract_captcha(data)
    if not sitekey or not rqdata:
        raise RuntimeError(f"No sitekey/rqdata from Riot: {json.dumps(data)[:500]}")
    return sitekey, rqdata, session


def submit_token(session, username: str, password: str, token: str) -> dict:
    # Riot Client flow submits captcha as: "hcaptcha <token>"
    payload = {
        "type": "auth",
        "remember": False,
        "language": "en_US",
        "riot_identity": {
            "username": username,
            "password": password,
            "captcha": f"hcaptcha {token}",
        },
    }
    r = session.put(LOGIN_API, json=payload, timeout=60)
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
    providers = configured_providers()
    print(f"proxy={to_http_url(proxy).split('@')[-1]}", flush=True)
    print(f"providers={[p for p, _ in providers]}", flush=True)
    if not providers:
        print("No solver API keys in .env — nothing to probe.", flush=True)
        return 1

    report: list[dict] = []
    # Fresh challenge per provider (rqdata is single-use)
    for name, key in providers:
        row: dict = {"provider": name}
        print(f"\n=== {name} ===", flush=True)
        try:
            sitekey, rqdata, session = fetch_challenge(proxy, user, password)
            row["sitekey"] = sitekey
            row["rqdata_len"] = len(rqdata)
            print(f"  challenge sitekey={sitekey} rqdata_len={len(rqdata)}", flush=True)

            # Capless allowlist requires authenticate.riotgames.com/*
            # CapMonster/2Captcha Riot scripts often use auth.riotgames.com
            if name == "capless":
                website_url = "https://authenticate.riotgames.com/"
            elif name == "capsolver":
                website_url = "https://authenticate.riotgames.com/"
            else:
                website_url = (
                    os.getenv("HCAPTCHA_WEBSITE_URL") or "https://auth.riotgames.com"
                )
            t0 = time.time()
            try:
                token = solve_with_provider(
                    name,
                    key,
                    website_url=website_url,
                    website_key=sitekey,
                    rqdata=rqdata,
                    user_agent=DEFAULT_UA,
                    proxy=proxy,
                    timeout=float(os.getenv("CAPTCHA_TIMEOUT") or "120"),
                )
                row["solve"] = "ok"
                row["token_prefix"] = token[:12]
                row["token_len"] = len(token)
                row["solve_s"] = round(time.time() - t0, 1)
                print(
                    f"  token ok prefix={token[:12]}… len={len(token)} "
                    f"in {row['solve_s']}s",
                    flush=True,
                )
            except CaptchaSolverError as exc:
                row["solve"] = "fail"
                row["solve_error"] = str(exc)[:300]
                row["solve_s"] = round(time.time() - t0, 1)
                print(f"  SOLVE FAIL: {exc}", flush=True)
                report.append(row)
                continue

            result = submit_token(session, user, password, token)
            row["riot_type"] = result.get("type")
            row["riot_error"] = result.get("error")
            row["riot_http"] = result.get("http")
            # Success-ish shapes
            if result.get("type") in ("success", "multifactor", "login"):
                row["riot_accept"] = True
            elif result.get("error") == "invalid_request" or result.get("captcha"):
                row["riot_accept"] = False
                row["riot_new_captcha"] = bool(result.get("captcha"))
            else:
                row["riot_accept"] = False
            print(
                f"  Riot submit type={row.get('riot_type')} error={row.get('riot_error')} "
                f"accept={row.get('riot_accept')}",
                flush=True,
            )
        except Exception as exc:
            row["fatal"] = str(exc)[:300]
            print(f"  FATAL: {exc}", flush=True)
        report.append(row)

    out = ROOT / "debug" / "hcaptcha_approaches_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nWrote {out}", flush=True)
    print(json.dumps(report, indent=2), flush=True)

    # Non-zero if nothing was accepted by Riot
    if any(r.get("riot_accept") for r in report):
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
