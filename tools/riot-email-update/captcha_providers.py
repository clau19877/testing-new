"""
Alternate hCaptcha enterprise solvers for Riot.

Approaches (all need fresh per-challenge rqdata + same egress IP as submit):

  1. capmonster  — CapMonster Cloud HCaptchaTask + customData=rqdata
                   (used by open-source Valorant/Riot auth scripts)
  2. twocaptcha  — 2Captcha HCaptchaTask + data=rqdata + matching userAgent
  3. nonecap     — NoneCap type=hcaptcha_enterprise + rqdata
  4. capless     — Capless /solve (already integrated; soft-fails on Riot verify)
  5. capsolver   — CapSolver (hard-rejects Riot sitekey)

Token APIs mint a P1_… response bound to rqdata. In-browser vision clicking
is a separate path (vision_captcha.py) and does not produce enterprise tokens.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from captcha import CaptchaSolverError
from proxyutil import to_http_url

CAPMONSTER_CREATE = "https://api.capmonster.cloud/createTask"
CAPMONSTER_RESULT = "https://api.capmonster.cloud/getTaskResult"
TWOCAPTCHA_CREATE = "https://api.2captcha.com/createTask"
TWOCAPTCHA_RESULT = "https://api.2captcha.com/getTaskResult"
NONECAP_SOLVE = "https://api.nonecap.com/v1/solves"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _poll_capmonster_style(
    *,
    result_url: str,
    client_key: str,
    task_id: Any,
    label: str,
    poll_interval: float = 3.0,
    timeout: float = 180.0,
) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        resp = requests.post(
            result_url,
            json={"clientKey": client_key, "taskId": task_id},
            timeout=60,
        )
        body = resp.json()
        if body.get("errorId"):
            raise CaptchaSolverError(f"{label} result error: {body}")
        status = body.get("status")
        if status == "ready":
            sol = body.get("solution") or {}
            token = (
                sol.get("gRecaptchaResponse")
                or sol.get("token")
                or sol.get("response")
            )
            if not token:
                raise CaptchaSolverError(f"{label} ready but no token: {body}")
            return token
        print(f"  {label} status={status or 'processing'}…", flush=True)
    raise CaptchaSolverError(f"{label} timed out after {timeout:.0f}s")


def solve_capmonster(
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    rqdata: str | None = None,
    user_agent: str = DEFAULT_UA,
    proxy: str | None = None,
    timeout: float = 180.0,
) -> str:
    """
    CapMonster Cloud — as of 2026-03 CapMonster removed HCaptchaTask support
    (ERROR_TASK_NOT_SUPPORTED). Kept for clarity / future task types.
    """
    raise CaptchaSolverError(
        "CapMonster Cloud no longer supports hCaptcha tasks "
        "(HCaptchaTask removed ~2026-03). Use twocaptcha / nonecap / capless."
    )


def solve_twocaptcha(
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    rqdata: str | None = None,
    user_agent: str = DEFAULT_UA,
    proxy: str | None = None,
    timeout: float = 180.0,
    require_proxy: bool = False,
    require_rqdata: bool = False,
) -> str:
    """
    2Captcha — pass rqdata as task.data; userAgent is required when data is set.
    Prefer a residential proxy matching the Riot submit IP (enterprise bind).
    """
    if require_rqdata and not rqdata:
        raise CaptchaSolverError("2Captcha enterprise strict: rqdata required")
    if require_proxy and not proxy:
        raise CaptchaSolverError("2Captcha enterprise strict: proxy required")
    # Always prefer proxy task when proxy is available — enterprise checks solve IP
    use_proxy = bool(proxy)
    task: dict[str, Any] = {
        "type": "HCaptchaTask" if use_proxy else "HCaptchaTaskProxyless",
        "websiteURL": website_url,
        "websiteKey": website_key,
        "userAgent": user_agent,
        "isInvisible": False,
    }
    if rqdata:
        task["data"] = rqdata
        # Enterprise sitekeys: mark explicitly so workers use enterprise path.
        # Override with TWOCAPTCHA_IS_ENTERPRISE=0 if workers stall on the flag.
        import os as _os

        if _os.getenv("TWOCAPTCHA_IS_ENTERPRISE", "1") not in ("0", "false", "False"):
            task["isEnterprise"] = True
    if use_proxy:
        assert proxy is not None
        url = to_http_url(proxy)
        without = url.split("://", 1)[1]
        creds, hostport = without.rsplit("@", 1)
        user, password = creds.split(":", 1)
        host, port = hostport.rsplit(":", 1)
        task["proxyType"] = "http"
        task["proxyAddress"] = host
        task["proxyPort"] = int(port)
        task["proxyLogin"] = user
        task["proxyPassword"] = password

    print(
        f"  2Captcha: createTask data={'yes' if rqdata else 'no'} "
        f"isEnterprise={bool(task.get('isEnterprise'))} "
        f"proxy={'yes' if use_proxy else 'no'} "
        f"ua={user_agent[:48]}…",
        flush=True,
    )
    create = requests.post(
        TWOCAPTCHA_CREATE,
        json={"clientKey": api_key, "task": task},
        timeout=60,
    )
    body = create.json()
    if body.get("errorId"):
        raise CaptchaSolverError(f"2Captcha create error: {body}")
    task_id = body.get("taskId")
    if task_id is None:
        raise CaptchaSolverError(f"2Captcha no taskId: {body}")
    return _poll_capmonster_style(
        result_url=TWOCAPTCHA_RESULT,
        client_key=api_key,
        task_id=task_id,
        label="2Captcha",
        timeout=timeout,
    )


def solve_nonecap(
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    rqdata: str | None = None,
    user_agent: str = DEFAULT_UA,
    proxy: str | None = None,
    timeout: float = 90.0,
    require_proxy: bool = False,
    require_rqdata: bool = False,
) -> str:
    """
    NoneCap — dedicated hCaptcha enterprise solver.
    POST /v1/solves?wait=N with type=hcaptcha_enterprise + rqdata.
    """
    if require_rqdata and not rqdata:
        raise CaptchaSolverError("NoneCap enterprise strict: rqdata required")
    if require_proxy and not proxy:
        raise CaptchaSolverError("NoneCap enterprise strict: proxy required")
    payload: dict[str, Any] = {
        "type": "hcaptcha_enterprise" if rqdata else "hcaptcha",
        "sitekey": website_key,
        "url": website_url,
    }
    if rqdata:
        payload["rqdata"] = rqdata
    if proxy:
        payload["proxy"] = to_http_url(proxy)
    # Best-effort UA bind (ignored by API if unsupported)
    if user_agent:
        payload["userAgent"] = user_agent
        payload["user_agent"] = user_agent

    wait = max(10, min(int(timeout), 90))
    url = f"{NONECAP_SOLVE}?wait={wait}"
    print(
        f"  NoneCap: {payload['type']} wait={wait}s "
        f"proxy={'yes' if proxy else 'no'} ua={user_agent[:48]}…",
        flush=True,
    )
    resp = requests.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=wait + 30,
    )
    try:
        body = resp.json()
    except Exception:
        raise CaptchaSolverError(f"NoneCap HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise CaptchaSolverError(f"NoneCap failed ({resp.status_code}): {body}")
    if body.get("status") not in ("solved", "ready", "success", None):
        # Some responses omit status when solved
        if not body.get("token"):
            raise CaptchaSolverError(f"NoneCap not solved: {body}")
    token = body.get("token") or (body.get("solution") or {}).get("token")
    if not token:
        raise CaptchaSolverError(f"NoneCap no token: {body}")
    return token


def solve_with_provider(
    provider: str,
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    rqdata: str | None = None,
    user_agent: str = DEFAULT_UA,
    proxy: str | None = None,
    timeout: float = 180.0,
    require_proxy: bool = False,
    require_rqdata: bool = False,
) -> str:
    """Dispatch to a named provider."""
    p = (provider or "").strip().lower()
    if require_rqdata and not (rqdata or "").strip():
        raise CaptchaSolverError(f"{p or provider}: enterprise strict requires rqdata")
    if require_proxy and not proxy:
        raise CaptchaSolverError(f"{p or provider}: enterprise strict requires proxy")
    if p in ("capmonster", "capmonstercloud"):
        return solve_capmonster(
            api_key,
            website_url=website_url,
            website_key=website_key,
            rqdata=rqdata,
            user_agent=user_agent,
            proxy=proxy,
            timeout=timeout,
        )
    if p in ("twocaptcha", "2captcha"):
        return solve_twocaptcha(
            api_key,
            website_url=website_url,
            website_key=website_key,
            rqdata=rqdata,
            user_agent=user_agent,
            proxy=proxy,
            timeout=timeout,
            require_proxy=require_proxy,
            require_rqdata=require_rqdata,
        )
    if p == "nonecap":
        return solve_nonecap(
            api_key,
            website_url=website_url,
            website_key=website_key,
            rqdata=rqdata,
            user_agent=user_agent,
            proxy=proxy,
            timeout=min(timeout, 90),
            require_proxy=require_proxy,
            require_rqdata=require_rqdata,
        )
    if p == "capless":
        from captcha import solve_capless

        if not proxy:
            raise CaptchaSolverError("Capless requires proxy")
        return solve_capless(
            api_key,
            website_url=website_url,
            website_key=website_key,
            proxy=proxy,
            rqdata=rqdata,
            timeout=timeout,
        )
    if p == "capsolver":
        from captcha import solve_capsolver

        return solve_capsolver(
            api_key,
            website_url=website_url,
            website_key=website_key,
            user_agent=user_agent,
            rqdata=rqdata,
            proxy=proxy,
            timeout=timeout,
        )
    raise CaptchaSolverError(f"Unknown captcha provider: {provider}")


def proxy_egress_ip(proxy: str, *, timeout: float = 30.0) -> str | None:
    """Return public IP seen through proxy (for solve/submit bind checks)."""
    url = to_http_url(proxy)
    for endpoint in (
        "https://api.ipify.org?format=json",
        "https://httpbin.org/ip",
    ):
        try:
            r = requests.get(
                endpoint,
                proxies={"http": url, "https": url},
                timeout=timeout,
            )
            if not r.ok:
                continue
            try:
                data = r.json()
            except Exception:
                text = (r.text or "").strip()
                return text or None
            return (
                data.get("ip")
                or data.get("origin")
                or (str(data) if data else None)
            )
        except Exception:
            continue
    return None
