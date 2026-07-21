"""Riot RSO login via authenticate.riotgames.com API + Capless captcha."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import requests

from captcha import CaptchaSolverError, solve_capless
from proxyutil import to_http_url

AUTH_BASE = "https://authenticate.riotgames.com"
LOGIN_API = f"{AUTH_BASE}/api/v1/login"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class RiotLoginError(RuntimeError):
    pass


def _session(proxy: str) -> requests.Session:
    s = requests.Session()
    proxy_url = to_http_url(proxy)
    s.proxies.update({"http": proxy_url, "https": proxy_url})
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": AUTH_BASE,
            "Referer": f"{AUTH_BASE}/",
        }
    )
    return s


def bootstrap_login(session: requests.Session, page_url: str) -> dict[str, Any]:
    """Hit the login page then GET/PUT login API to obtain captcha challenge."""
    # Establish cookies from the auth page
    session.get(page_url or AUTH_BASE, timeout=60)
    # Some flows need an initial PUT/GET
    r = session.get(LOGIN_API, timeout=60)
    if r.status_code == 405:
        r = session.put(LOGIN_API, json={"type": "auth", "remember": False}, timeout=60)
    try:
        data = r.json()
    except Exception:
        raise RiotLoginError(f"bootstrap login non-json ({r.status_code}): {r.text[:300]}")
    return data


def extract_captcha(data: dict[str, Any]) -> tuple[str | None, str | None]:
    captcha = data.get("captcha") or {}
    hcap = captcha.get("hcaptcha") or captcha.get("hcaptcha_enterprise") or {}
    if not hcap and isinstance(captcha, dict):
        # nested variants
        for v in captcha.values():
            if isinstance(v, dict) and ("key" in v or "data" in v):
                hcap = v
                break
    sitekey = hcap.get("key") or hcap.get("sitekey")
    rqdata = hcap.get("data") or hcap.get("rqdata")
    return sitekey, rqdata


def login_with_capless(
    *,
    username: str,
    password: str,
    capless_key: str,
    proxy: str,
    page_url: str,
) -> dict[str, Any]:
    """
    Perform Riot identity login.
    Returns the final login API JSON (should contain type success / multifactor / etc).
    """
    session = _session(proxy)
    print("  Riot API: bootstrapping login challenge…", flush=True)
    data = bootstrap_login(session, page_url)
    print(f"  Riot API bootstrap type={data.get('type')} keys={list(data.keys())}", flush=True)

    sitekey, rqdata = extract_captcha(data)
    if not sitekey:
        # Trigger captcha by attempting login without captcha
        probe = {
            "type": "auth",
            "remember": False,
            "language": "en_US",
            "riot_identity": {
                "username": username,
                "password": password,
            },
        }
        r = session.put(LOGIN_API, json=probe, timeout=60)
        try:
            data = r.json()
        except Exception:
            raise RiotLoginError(f"probe login failed ({r.status_code}): {r.text[:300]}")
        print(f"  Riot API probe type={data.get('type')} keys={list(data.keys())}", flush=True)
        sitekey, rqdata = extract_captcha(data)

    if not sitekey:
        raise RiotLoginError(f"No hCaptcha challenge in Riot response: {json.dumps(data)[:500]}")

    print(f"  Riot captcha sitekey={sitekey} rqdata={'yes' if rqdata else 'no'}", flush=True)
    token = solve_capless(
        capless_key,
        website_url=page_url if "authenticate.riotgames.com" in page_url else f"{AUTH_BASE}/",
        website_key=sitekey,
        proxy=proxy,
        rqdata=rqdata,
    )
    print(f"  Capless token ok ({len(token)} chars) — submitting login…", flush=True)

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
        result = r.json()
    except Exception:
        raise RiotLoginError(f"login submit non-json ({r.status_code}): {r.text[:400]}")

    print(
        f"  Riot login response type={result.get('type')} status={r.status_code} "
        f"keys={list(result.keys())}",
        flush=True,
    )

    # Persist cookies for Playwright by returning them
    result["_cookies"] = requests.utils.dict_from_cookiejar(session.cookies)
    result["_cookie_list"] = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
        for c in session.cookies
    ]
    result["_session"] = session  # caller may continue MFA
    return result


def submit_mfa_code(session: requests.Session, code: str) -> dict[str, Any]:
    payload = {
        "type": "multifactor",
        "remember": False,
        "multifactor": {
            "otp": code,
            "rememberDevice": True,
        },
    }
    # Some builds nest differently
    r = session.put(LOGIN_API, json=payload, timeout=60)
    try:
        return r.json()
    except Exception:
        raise RiotLoginError(f"MFA submit non-json ({r.status_code}): {r.text[:400]}")
