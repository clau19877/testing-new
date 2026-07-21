"""
Generalized Riot RSO login via authenticate.riotgames.com API + token solver.

Supports CapMonster-style customData=rqdata (common in open-source Riot auth),
plus Capless / 2Captcha / NoneCap via captcha_providers.solve_with_provider.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from captcha_providers import DEFAULT_UA, solve_with_provider
from proxyutil import to_http_url

AUTH_BASE = "https://authenticate.riotgames.com"
AUTH_RIOT = "https://auth.riotgames.com"
LOGIN_API = f"{AUTH_BASE}/api/v1/login"
AUTHZ_API = f"{AUTH_RIOT}/api/v1/authorization"
USER_AGENT = DEFAULT_UA
# Open-source Valorant/Riot auth uses a RiotClient UA for the RSO API surface
RIOT_CLIENT_UA = (
    "RiotClient/63.0.9.4909983.4789131 rso-auth (Windows;10;;Professional, x64)"
)


class RiotLoginError(RuntimeError):
    pass


def _session(proxy: str, *, riot_client: bool = True) -> requests.Session:
    s = requests.Session()
    proxy_url = to_http_url(proxy)
    s.proxies.update({"http": proxy_url, "https": proxy_url})
    ua = RIOT_CLIENT_UA if riot_client else USER_AGENT
    s.headers.update(
        {
            "User-Agent": ua,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }
    )
    if riot_client:
        s.cookies.update({"tdid": "", "asid": "", "did": "", "clid": ""})
    else:
        s.headers.update(
            {
                "Origin": AUTH_BASE,
                "Referer": f"{AUTH_BASE}/",
                "User-Agent": USER_AGENT,
            }
        )
    return s


def bootstrap_login(session: requests.Session, page_url: str = "") -> dict[str, Any]:
    """
    Riot Client–style bootstrap that yields a fresh hCaptcha sitekey+rqdata.

    Matches open-source Valorant auth:
      POST auth.riotgames.com/api/v1/authorization
      POST authenticate.riotgames.com/api/v1/login  (type=auth, riot_identity state)
    """
    # 1) Start RSO authorization (sets cookies / country)
    try:
        session.post(
            AUTHZ_API,
            json={
                "client_id": "riot-client",
                "nonce": "probe-nonce",
                "redirect_uri": "http://localhost/redirect",
                "response_type": "token id_token",
                "scope": "account openid",
            },
            timeout=60,
        )
    except Exception as exc:
        print(f"  authz warn: {exc}", flush=True)

    # 2) Ask authenticate API for a captcha challenge
    r = session.post(
        LOGIN_API,
        json={
            "clientId": "riot-client",
            "language": "",
            "platform": "windows",
            "remember": False,
            "riot_identity": {"language": "en_US", "state": "auth"},
            "type": "auth",
        },
        timeout=60,
    )
    try:
        data = r.json()
    except Exception:
        raise RiotLoginError(f"bootstrap login non-json ({r.status_code}): {r.text[:300]}")
    return data


def extract_captcha(data: dict[str, Any]) -> tuple[str | None, str | None]:
    captcha = data.get("captcha") or {}
    hcap = captcha.get("hcaptcha") or captcha.get("hcaptcha_enterprise") or {}
    if not hcap and isinstance(captcha, dict):
        for v in captcha.values():
            if isinstance(v, dict) and ("key" in v or "data" in v):
                hcap = v
                break
    sitekey = hcap.get("key") or hcap.get("sitekey")
    rqdata = hcap.get("data") or hcap.get("rqdata")
    return sitekey, rqdata


def login_with_solver(
    *,
    username: str,
    password: str,
    provider: str,
    api_key: str,
    proxy: str,
    page_url: str,
    website_url: str | None = None,
) -> dict[str, Any]:
    """
    Perform Riot identity login using a token solver.
    Returns the final login API JSON (success / multifactor / error / …).
    """
    session = _session(proxy, riot_client=True)
    print("  Riot API: bootstrapping login challenge…", flush=True)
    data = bootstrap_login(session, page_url)
    print(f"  Riot API bootstrap type={data.get('type')} keys={list(data.keys())}", flush=True)

    sitekey, rqdata = extract_captcha(data)
    if not sitekey:
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
    # Open-source Riot auth commonly uses https://auth.riotgames.com as websiteURL
    solve_url = website_url or "https://auth.riotgames.com"
    if provider == "capless":
        # Capless allowlist uses authenticate.riotgames.com/*
        solve_url = f"{AUTH_BASE}/"

    token = solve_with_provider(
        provider,
        api_key,
        website_url=solve_url,
        website_key=sitekey,
        rqdata=rqdata,
        user_agent=USER_AGENT,
        proxy=proxy,
    )
    print(f"  {provider} token ok ({len(token)} chars) — submitting login…", flush=True)

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

    result["_cookies"] = requests.utils.dict_from_cookiejar(session.cookies)
    result["_cookie_list"] = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
        for c in session.cookies
    ]
    result["_session"] = session
    return result


def login_with_capless(
    *,
    username: str,
    password: str,
    capless_key: str,
    proxy: str,
    page_url: str,
) -> dict[str, Any]:
    """Back-compat wrapper."""
    return login_with_solver(
        username=username,
        password=password,
        provider="capless",
        api_key=capless_key,
        proxy=proxy,
        page_url=page_url,
    )


def submit_mfa_code(session: requests.Session, code: str) -> dict[str, Any]:
    payload = {
        "type": "multifactor",
        "remember": False,
        "multifactor": {
            "otp": code,
            "rememberDevice": True,
        },
    }
    r = session.put(LOGIN_API, json=payload, timeout=60)
    try:
        return r.json()
    except Exception:
        raise RiotLoginError(f"MFA submit non-json ({r.status_code}): {r.text[:400]}")
