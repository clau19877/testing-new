"""
Alternate hCaptcha enterprise solvers for Riot.

Approaches (all need fresh per-challenge rqdata + same egress IP as submit):

  1. aycd        — AYCD AutoSolve hub (OneClick / AI / 3rd-party on AYCD side)
  2. capmonster  — CapMonster Cloud HCaptchaTask + customData=rqdata
                   (used by open-source Valorant/Riot auth scripts)
  3. twocaptcha  — 2Captcha HCaptchaTask + data=rqdata + matching userAgent
  4. nonecap     — NoneCap type=hcaptcha_enterprise + rqdata
  5. capless     — Capless /solve (already integrated; soft-fails on Riot verify)
  6. capsolver   — CapSolver (hard-rejects Riot sitekey)

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
NOPECHA_TOKEN = "https://api.nopecha.com/token/"

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


def solve_nopecha(
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
    NopeCHA Token API — supports Enterprise via data.rqdata + matching proxy.
    https://developers.nopecha.com/token/hcaptcha/
    """
    if require_rqdata and not rqdata:
        raise CaptchaSolverError("NopeCHA enterprise strict: rqdata required")
    if require_proxy and not proxy:
        raise CaptchaSolverError("NopeCHA enterprise strict: proxy required")

    payload: dict[str, Any] = {
        "key": api_key,
        "type": "hcaptcha",
        "sitekey": website_key,
        "url": website_url,
        "useragent": user_agent,
    }
    if rqdata:
        payload["data"] = {"rqdata": rqdata}
    if proxy:
        url = to_http_url(proxy)
        without = url.split("://", 1)[1]
        creds, hostport = without.rsplit("@", 1)
        user, password = creds.split(":", 1)
        host, port = hostport.rsplit(":", 1)
        payload["proxy"] = {
            "scheme": "http",
            "host": host,
            "port": str(port),
            "username": user,
            "password": password,
        }

    print(
        f"  NopeCHA: token rqdata={'yes' if rqdata else 'no'} "
        f"proxy={'yes' if proxy else 'no'}…",
        flush=True,
    )
    create = requests.post(NOPECHA_TOKEN, json=payload, timeout=60)
    try:
        body = create.json()
    except Exception:
        raise CaptchaSolverError(f"NopeCHA HTTP {create.status_code}: {create.text[:300]}")
    if body.get("error"):
        raise CaptchaSolverError(f"NopeCHA create error: {body}")
    job_id = body.get("data")
    if not job_id:
        # Some responses return the token inline
        if isinstance(body.get("data"), str) and body["data"].startswith("P1_"):
            return body["data"]
        raise CaptchaSolverError(f"NopeCHA no job id: {body}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2.0)
        r = requests.get(
            NOPECHA_TOKEN,
            params={"key": api_key, "id": job_id},
            timeout=60,
        )
        try:
            result = r.json()
        except Exception:
            continue
        if result.get("error") == 14 or result.get("message") == "Incomplete job":
            print("  NopeCHA status=processing…", flush=True)
            continue
        if result.get("error"):
            raise CaptchaSolverError(f"NopeCHA result error: {result}")
        token = result.get("data")
        if token and isinstance(token, str) and len(token) > 20:
            return token
        print(f"  NopeCHA status={result}…", flush=True)
    raise CaptchaSolverError(f"NopeCHA timed out after {timeout:.0f}s")


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
    is_invisible: bool = False,
) -> str:
    """Dispatch to a named provider."""
    import os

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
    if p in ("nopecha", "nope"):
        return solve_nopecha(
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
    if p in ("multibot", "multibot_token"):
        return solve_multibot_token(
            api_key,
            website_url=website_url,
            website_key=website_key,
            rqdata=rqdata,
            proxy=proxy,
            timeout=timeout,
            require_proxy=require_proxy,
            require_rqdata=require_rqdata,
        )
    if p in ("aycd", "autosolve", "aycd_autosolve"):
        from aycd_autosolve import solve_aycd

        aycd_timeout = float(os.getenv("AYCD_TIMEOUT") or timeout or 300)
        return solve_aycd(
            api_key,
            website_url=website_url,
            website_key=website_key,
            rqdata=rqdata,
            user_agent=user_agent,
            proxy=proxy,
            is_invisible=is_invisible,
            timeout=aycd_timeout,
        )
    raise CaptchaSolverError(f"Unknown captcha provider: {provider}")


def solve_multibot_token(
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    rqdata: str | None = None,
    proxy: str | None = None,
    timeout: float = 180.0,
    require_proxy: bool = False,
    require_rqdata: bool = False,
) -> str:
    """Multibot classic hCaptcha token API (enterprise=1 + data=rqdata)."""
    if require_rqdata and not rqdata:
        raise CaptchaSolverError("Multibot enterprise strict: rqdata required")
    if require_proxy and not proxy:
        raise CaptchaSolverError("Multibot enterprise strict: proxy required")
    params: dict[str, Any] = {
        "key": api_key,
        "method": "hcaptcha",
        "pageurl": website_url,
        "sitekey": website_key,
        "enterprise": "1" if rqdata else "0",
        "json": "1",
    }
    if rqdata:
        params["data"] = rqdata
    if proxy:
        params["proxy"] = to_http_url(proxy)
    print(
        f"  Multibot token: enterprise={params['enterprise']} "
        f"proxy={'yes' if proxy else 'no'}…",
        flush=True,
    )
    files = {k: (None, str(v)) for k, v in params.items()}
    create = requests.post("https://api.multibot.cloud/in.php", files=files, timeout=30)
    try:
        body = create.json()
    except Exception:
        raise CaptchaSolverError(f"Multibot token HTTP {create.status_code}: {create.text[:300]}")
    if body.get("status") != 1 and not body.get("request"):
        raise CaptchaSolverError(f"Multibot token create failed: {body}")
    req_id = body.get("request")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3.0)
        r = requests.get(
            "https://api.multibot.cloud/res.php",
            params={"key": api_key, "action": "get", "id": req_id, "json": 1},
            timeout=30,
        )
        try:
            result = r.json()
        except Exception:
            continue
        if result.get("status") == 1 and result.get("request"):
            return str(result["request"])
        if result.get("request") == "CAPCHA_NOT_READY" or result.get("request") == "CAPTCHA_NOT_READY":
            print("  Multibot token status=processing…", flush=True)
            continue
        if result.get("status") == 0 and "NOT_READY" in str(result.get("request", "")).upper():
            print("  Multibot token status=processing…", flush=True)
            continue
        raise CaptchaSolverError(f"Multibot token result: {result}")
    raise CaptchaSolverError(f"Multibot token timed out after {timeout:.0f}s")


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
