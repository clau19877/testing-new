"""
Generalized Riot RSO login via authenticate.riotgames.com API + token solver.

Supports CapMonster-style customData=rqdata (common in open-source Riot auth),
plus Capless / 2Captcha / NoneCap via captcha_providers.solve_with_provider.

Strict enterprise mode (default for token probes):
  - same User-Agent on Riot session AND solver task
  - residential proxy required (solve IP == submit IP)
  - fresh rqdata required (hard-fail if missing)
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

from captcha_providers import DEFAULT_UA, proxy_egress_ip, solve_with_provider
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


def resolve_ua(ua_mode: str | None = None) -> str:
    """
    ua_mode:
      chrome  — browser Chrome UA (DEFAULT_UA)
      riot    — RiotClient UA
      <other> — treated as a literal User-Agent string
    """
    mode = (ua_mode or os.getenv("RIOT_UA_MODE") or "chrome").strip()
    lower = mode.lower()
    if lower in ("chrome", "browser", "default"):
        return DEFAULT_UA
    if lower in ("riot", "riotclient", "client"):
        return RIOT_CLIENT_UA
    return mode


def _session(proxy: str, *, user_agent: str | None = None, riot_client: bool | None = None) -> requests.Session:
    s = requests.Session()
    proxy_url = to_http_url(proxy)
    s.proxies.update({"http": proxy_url, "https": proxy_url})
    if user_agent is None:
        # Back-compat: riot_client=True → RiotClient UA
        if riot_client is None:
            riot_client = True
        user_agent = RIOT_CLIENT_UA if riot_client else USER_AGENT
    else:
        riot_client = user_agent.startswith("RiotClient/")
    s.headers.update(
        {
            "User-Agent": user_agent,
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
    ua_mode: str | None = None,
    require_rqdata: bool = True,
    require_proxy: bool = True,
    check_egress: bool = True,
) -> dict[str, Any]:
    """
    Perform Riot identity login using a token solver.
    Returns the final login API JSON (success / multifactor / error / …).

    Strict defaults: matched UA, required rqdata+proxy, optional egress IP log.
    """
    if require_proxy and not (proxy or "").strip():
        raise RiotLoginError("enterprise strict: proxy required for solve+submit bind")

    user_agent = resolve_ua(ua_mode)
    session = _session(proxy, user_agent=user_agent)
    print(
        f"  Riot API: bootstrapping login challenge (ua_mode={ua_mode or 'chrome'}, "
        f"ua={user_agent[:56]}…)…",
        flush=True,
    )

    egress = None
    if check_egress and proxy:
        egress = proxy_egress_ip(proxy)
        print(f"  proxy egress IP={egress or 'unknown'}", flush=True)

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
    if require_rqdata and not rqdata:
        raise RiotLoginError("enterprise strict: no rqdata in Riot captcha challenge")

    print(
        f"  Riot captcha sitekey={sitekey} rqdata={'yes' if rqdata else 'no'} "
        f"len={len(rqdata or '')}",
        flush=True,
    )
    # Open-source Riot auth commonly uses https://auth.riotgames.com as websiteURL
    solve_url = website_url or os.getenv("HCAPTCHA_WEBSITE_URL") or "https://authenticate.riotgames.com/"
    if provider == "capless":
        # Capless allowlist uses authenticate.riotgames.com/*
        solve_url = f"{AUTH_BASE}/"

    token = solve_with_provider(
        provider,
        api_key,
        website_url=solve_url,
        website_key=sitekey,
        rqdata=rqdata,
        user_agent=user_agent,
        proxy=proxy,
        require_proxy=require_proxy,
        require_rqdata=require_rqdata,
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
    result["_meta"] = {
        "user_agent": user_agent,
        "website_url": solve_url,
        "sitekey": sitekey,
        "rqdata_len": len(rqdata or ""),
        "egress_ip": egress,
        "provider": provider,
        "token_prefix": token[:12],
    }
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
