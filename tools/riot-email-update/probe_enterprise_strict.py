#!/usr/bin/env python3
"""
Strict enterprise hCaptcha token probe for Riot.

Enforces the binding triad that soft-failed in earlier probes:
  1. fresh rqdata (hard-required)
  2. same residential proxy for challenge + solver + submit (egress logged)
  3. same User-Agent on Riot session and solver task

Matrix (default):
  providers: twocaptcha, nonecap  (skip CapSolver — sitekey rejected)
  ua_mode:   chrome, riot
  website:   authenticate.riotgames.com/, auth.riotgames.com

Usage:
  cd tools/riot-email-update
  .venv/bin/python probe_enterprise_strict.py

Env:
  PROXY_INDEX, PROXY_LIST, RIOT_USERNAME, RIOT_PASSWORD
  TWOCAPTCHA_API_KEY, NONECAP_API_KEY
  CAPTCHA_TIMEOUT (default 180)
  STRICT_PROVIDERS=twocaptcha,nonecap
  STRICT_UA_MODES=chrome,riot
  STRICT_WEBSITES=authenticate,auth
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
from captcha_providers import proxy_egress_ip, solve_with_provider  # noqa: E402
from proxyutil import load_proxy_list, to_http_url  # noqa: E402
from riot_api_login import (  # noqa: E402
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
    raw = (
        os.getenv("CAPLESS_PROXY")
        or os.getenv("BROWSER_PROXY")
        or os.getenv("CAPTCHA_PROXY")
        or ""
    )
    if not raw:
        raise SystemExit("No proxy configured (PROXY_LIST / CAPLESS_PROXY)")
    return raw


def providers() -> list[tuple[str, str]]:
    wanted = [
        p.strip().lower()
        for p in (os.getenv("STRICT_PROVIDERS") or "twocaptcha,nonecap").split(",")
        if p.strip()
    ]
    keys = {
        "twocaptcha": (
            os.getenv("TWOCAPTCHA_API_KEY")
            or os.getenv("TWO_CAPTCHA_API_KEY")
            or os.getenv("2CAPTCHA_API_KEY")
        ),
        "nonecap": os.getenv("NONECAP_API_KEY"),
        "capless": os.getenv("CAPLESS_API_KEY"),
        "capsolver": os.getenv("CAPSOLVER_API_KEY"),
    }
    out: list[tuple[str, str]] = []
    for name in wanted:
        key = (keys.get(name) or "").strip()
        if key:
            out.append((name, key))
        else:
            print(f"[skip] {name}: no API key", flush=True)
    return out


def website_urls() -> list[tuple[str, str]]:
    wanted = [
        w.strip().lower()
        for w in (os.getenv("STRICT_WEBSITES") or "authenticate,auth").split(",")
        if w.strip()
    ]
    mapping = {
        "authenticate": ("authenticate", "https://authenticate.riotgames.com/"),
        "authn": ("authenticate", "https://authenticate.riotgames.com/"),
        "auth": ("auth", "https://auth.riotgames.com"),
        "auth.riot": ("auth", "https://auth.riotgames.com"),
    }
    out: list[tuple[str, str]] = []
    for w in wanted:
        if w in mapping:
            out.append(mapping[w])
        elif w.startswith("http"):
            out.append((w, w))
    return out or [("authenticate", "https://authenticate.riotgames.com/")]


def ua_modes() -> list[str]:
    return [
        m.strip()
        for m in (os.getenv("STRICT_UA_MODES") or "chrome,riot").split(",")
        if m.strip()
    ]


def fetch_challenge(proxy: str, username: str, password: str, user_agent: str):
    session = _session(proxy, user_agent=user_agent)
    data = bootstrap_login(session)
    sitekey, rqdata = extract_captcha(data)
    if not sitekey or not rqdata:
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
    provs = providers()
    print(f"proxy={to_http_url(proxy).split('@')[-1]}", flush=True)
    egress = proxy_egress_ip(proxy)
    print(f"egress_ip={egress or 'unknown'}", flush=True)
    print(f"providers={[p for p, _ in provs]}", flush=True)
    print(f"ua_modes={ua_modes()}", flush=True)
    print(f"websites={[w for w, _ in website_urls()]}", flush=True)
    if not provs:
        print("No solver API keys — nothing to probe.", flush=True)
        return 1

    timeout = float(os.getenv("CAPTCHA_TIMEOUT") or "180")
    report: list[dict] = []

    for name, key in provs:
        for ua_mode in ua_modes():
            for site_label, website_url in website_urls():
                label = f"{name}|ua={ua_mode}|site={site_label}|proxy=yes"
                row: dict = {
                    "label": label,
                    "provider": name,
                    "ua_mode": ua_mode,
                    "website_url": website_url,
                    "egress_ip": egress,
                    "strict": {
                        "rqdata": True,
                        "proxy": True,
                        "matched_ua": True,
                    },
                }
                print(f"\n=== {label} ===", flush=True)
                try:
                    ua = resolve_ua(ua_mode)
                    row["user_agent"] = ua
                    sitekey, rqdata, session = fetch_challenge(
                        proxy, user, password, ua
                    )
                    row["sitekey"] = sitekey
                    row["rqdata_len"] = len(rqdata)
                    print(
                        f"  challenge sitekey={sitekey} rqdata_len={len(rqdata)} "
                        f"ua={ua[:48]}…",
                        flush=True,
                    )
                    t0 = time.time()
                    try:
                        token = solve_with_provider(
                            name,
                            key,
                            website_url=website_url,
                            website_key=sitekey,
                            rqdata=rqdata,
                            user_agent=ua,
                            proxy=proxy,
                            timeout=timeout,
                            require_proxy=True,
                            require_rqdata=True,
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
                    if result.get("type") in ("success", "multifactor", "login"):
                        row["riot_accept"] = True
                    elif result.get("error") == "invalid_request" or result.get(
                        "captcha"
                    ):
                        row["riot_accept"] = False
                        row["riot_new_captcha"] = bool(result.get("captcha"))
                    else:
                        row["riot_accept"] = False
                    print(
                        f"  Riot submit type={row.get('riot_type')} "
                        f"error={row.get('riot_error')} "
                        f"accept={row.get('riot_accept')}",
                        flush=True,
                    )
                except Exception as exc:
                    row["fatal"] = str(exc)[:300]
                    print(f"  FATAL: {exc}", flush=True)
                report.append(row)

    out = ROOT / "debug" / "hcaptcha_enterprise_strict_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nWrote {out}", flush=True)
    print(json.dumps(report, indent=2), flush=True)

    accepted = [r for r in report if r.get("riot_accept")]
    if accepted:
        print(f"\nACCEPTED rows: {len(accepted)}", flush=True)
        return 0
    print("\nNo Riot-accepted tokens under strict bind constraints.", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
