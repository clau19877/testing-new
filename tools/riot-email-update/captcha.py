"""Captcha solvers for Riot hCaptcha.

CapSolver does NOT support Riot's sitekey (returns ERROR_INVALID_TASK_DATA /
"We don't support this service"). Capless explicitly lists Riot Games as
supported and is the recommended provider for this script.
"""

from __future__ import annotations

import time
from typing import Any

import requests

CAPSOLVER_CREATE = "https://api.capsolver.com/createTask"
CAPSOLVER_RESULT = "https://api.capsolver.com/getTaskResult"
CAPLESS_SOLVE = "https://capless.lol/solve"

# Known Riot hCaptcha sitekeys
RIOT_SITEKEYS = {
    "authenticate.riotgames.com": "019f1553-3845-481c-a6f5-5a60ccf6d830",
    "auth.riotgames.com": "019f1553-3845-481c-a6f5-5a60ccf6d830",
    "account.riotgames.com": "b3c619fc-4b72-4838-9bf2-398888588d62",
    "recovery.riotgames.com": "db18a187-7b77-4dac-a6cb-6dd5215973cf",
}


class CaptchaSolverError(RuntimeError):
    pass


# Back-compat alias
CapSolverError = CaptchaSolverError


def known_sitekey_for_url(url: str) -> str | None:
    for host, key in RIOT_SITEKEYS.items():
        if host in url:
            return key
    return None


def extract_hcaptcha_params(page) -> dict[str, str | None]:
    """Read sitekey / rqdata from the live Riot auth page."""
    params = page.evaluate(
        """() => {
            const out = { sitekey: null, rqdata: null, isInvisible: false };

            const el = document.querySelector(
              '[data-sitekey], .h-captcha, [data-hcaptcha-sitekey]'
            );
            if (el) {
              out.sitekey =
                el.getAttribute('data-sitekey') ||
                el.getAttribute('data-hcaptcha-sitekey');
              out.rqdata =
                el.getAttribute('data-rqdata') ||
                el.getAttribute('data-hcaptcha-rqdata');
              out.isInvisible =
                el.getAttribute('data-size') === 'invisible' ||
                el.classList.contains('h-captcha-invisible');
            }

            if (!out.sitekey) {
              const iframe = document.querySelector(
                'iframe[src*="hcaptcha.com"], iframe[src*="newassets.hcaptcha.com"]'
              );
              if (iframe && iframe.src) {
                try {
                  const u = new URL(iframe.src);
                  out.sitekey = u.searchParams.get('sitekey') || out.sitekey;
                } catch (e) {}
              }
            }

            const html = document.documentElement.innerHTML;
            if (!out.sitekey) {
              const m = html.match(
                /sitekey["'\\s:=]+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i
              );
              if (m) out.sitekey = m[1];
            }
            if (!out.rqdata) {
              const m = html.match(/rqdata["'\\s:=]+([^"'\\s&<>]+)/i);
              if (m) out.rqdata = m[1];
            }

            return out;
        }"""
    )
    return params


def solve_capsolver(
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    user_agent: str,
    rqdata: str | None = None,
    is_invisible: bool = False,
    proxy: str | None = None,
    poll_interval: float = 3.0,
    timeout: float = 180.0,
) -> str:
    task: dict[str, Any] = {
        "type": "HCaptchaTask" if proxy else "HCaptchaTaskProxyLess",
        "websiteURL": website_url,
        "websiteKey": website_key,
        "userAgent": user_agent,
        "isInvisible": is_invisible,
    }
    if rqdata:
        task["enterprisePayload"] = {"rqdata": rqdata}
    if proxy:
        task["proxy"] = proxy

    create = requests.post(
        CAPSOLVER_CREATE,
        json={"clientKey": api_key, "task": task},
        timeout=60,
    )
    try:
        created = create.json()
    except Exception:
        raise CaptchaSolverError(f"CapSolver HTTP {create.status_code}: {create.text}")
    if create.status_code >= 400 or created.get("errorId"):
        raise CaptchaSolverError(
            f"CapSolver createTask failed: {created.get('errorCode')} "
            f"{created.get('errorDescription') or create.text}"
        )

    task_id = created.get("taskId")
    if not task_id:
        raise CaptchaSolverError(f"CapSolver createTask returned no taskId: {created}")

    print(f"  CapSolver task created: {task_id}", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        result = requests.post(
            CAPSOLVER_RESULT,
            json={"clientKey": api_key, "taskId": task_id},
            timeout=60,
        )
        body = result.json()
        if body.get("errorId"):
            raise CaptchaSolverError(
                f"CapSolver getTaskResult failed: {body.get('errorCode')} "
                f"{body.get('errorDescription')}"
            )
        status = body.get("status")
        if status == "ready":
            solution = body.get("solution") or {}
            token = (
                solution.get("gRecaptchaResponse")
                or solution.get("token")
                or solution.get("response")
            )
            if not token:
                raise CaptchaSolverError(f"CapSolver ready but no token: {body}")
            return token
        if status == "failed":
            raise CaptchaSolverError(f"CapSolver solve failed: {body}")
        print(f"  CapSolver status: {status or 'processing'}…", flush=True)

    raise CaptchaSolverError(f"CapSolver timed out after {timeout:.0f}s")


def solve_capless(
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    proxy: str,
    rqdata: str | None = None,
    timeout: float = 180.0,
) -> str:
    """
    Capless (https://capless.lol) — supports Riot authenticate.riotgames.com.
    Requires a residential/mobile HTTP proxy matching the browser egress IP.
    """
    # Capless expects the page URL to match its allowlist patterns.
    site = website_url
    if "authenticate.riotgames.com" in website_url:
        # Keep full URL (query allowed by their /* wildcard)
        site = website_url
    elif "account.riotgames.com" in website_url:
        site = website_url

    proxy_fmt = proxy
    if proxy_fmt and "://" not in proxy_fmt and proxy_fmt.count(":") >= 3:
        # CapSolver-style http:ip:port:user:pass → http://user:pass@ip:port
        parts = proxy_fmt.split(":")
        scheme, ip, port, user, password = parts[0], parts[1], parts[2], parts[3], ":".join(parts[4:])
        proxy_fmt = f"{scheme}://{user}:{password}@{ip}:{port}"

    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    payload: dict[str, Any] = {
        "type": "hcaptcha",
        "site": site,
        "sitekey": website_key,
        "proxy": proxy_fmt,
    }
    if rqdata:
        payload["rqdata"] = rqdata

    print("  Capless: submitting solve…", flush=True)
    resp = requests.post(CAPLESS_SOLVE, json=payload, headers=headers, timeout=timeout)
    try:
        data = resp.json()
    except Exception:
        raise CaptchaSolverError(f"Capless HTTP {resp.status_code}: {resp.text}")

    if resp.status_code >= 400 or data.get("status") != "success":
        raise CaptchaSolverError(
            f"Capless failed ({resp.status_code}): {data.get('error') or data}"
        )
    token = data.get("token")
    if not token:
        raise CaptchaSolverError(f"Capless success but no token: {data}")
    return token


def inject_hcaptcha_token(page, token: str) -> None:
    page.evaluate(
        """(token) => {
            const setVal = (el) => {
              if (!el) return;
              el.value = token;
              el.innerHTML = token;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            };

            document.querySelectorAll(
              '[name="h-captcha-response"], textarea[name="h-captcha-response"],
               [name="g-recaptcha-response"], textarea[name="g-recaptcha-response"]'
            ).forEach(setVal);

            const ensure = (name) => {
              let el = document.querySelector(`[name="${name}"]`);
              if (!el) {
                el = document.createElement('textarea');
                el.name = name;
                el.style.display = 'none';
                document.body.appendChild(el);
              }
              setVal(el);
            };
            ensure('h-captcha-response');
            ensure('g-recaptcha-response');

            const cbNames = [
              'onCaptchaSuccess',
              'captchaCallback',
              'hcaptchaCallback',
              'onSuccess',
            ];
            for (const name of cbNames) {
              if (typeof window[name] === 'function') {
                try { window[name](token); } catch (e) {}
              }
            }

            const widget = document.querySelector('[data-callback]');
            if (widget) {
              const name = widget.getAttribute('data-callback');
              if (name && typeof window[name] === 'function') {
                try { window[name](token); } catch (e) {}
              }
            }
        }""",
        token,
    )


def solve_and_inject(
    page,
    *,
    provider: str,
    api_key: str,
    proxy: str | None = None,
) -> bool:
    """
    Detect hCaptcha on the current page, solve, inject token.
    provider: "capless" (recommended for Riot) or "capsolver"
    """
    website_url = page.url
    user_agent = page.evaluate("() => navigator.userAgent")
    params = extract_hcaptcha_params(page)
    sitekey = params.get("sitekey") or known_sitekey_for_url(website_url)
    rqdata = params.get("rqdata")
    is_invisible = bool(params.get("isInvisible"))

    if not sitekey:
        print("  No hCaptcha sitekey found on page — skipping solver.", flush=True)
        return False

    print(f"  hCaptcha sitekey: {sitekey}", flush=True)
    if rqdata:
        print(f"  rqdata present ({len(rqdata)} chars)", flush=True)
    else:
        print("  No rqdata found (will try without enterprise payload)", flush=True)

    provider = (provider or "capless").strip().lower()
    if provider == "capsolver":
        token = solve_capsolver(
            api_key,
            website_url=website_url,
            website_key=sitekey,
            user_agent=user_agent,
            rqdata=rqdata,
            is_invisible=is_invisible,
            proxy=proxy,
        )
    elif provider == "capless":
        if not proxy:
            raise CaptchaSolverError(
                "Capless requires a proxy (set CAPLESS_PROXY / CAPTCHA_PROXY). "
                "Use a residential proxy, ideally same IP as this browser."
            )
        token = solve_capless(
            api_key,
            website_url=website_url,
            website_key=sitekey,
            proxy=proxy,
            rqdata=rqdata,
        )
    else:
        raise CaptchaSolverError(f"Unknown captcha provider: {provider}")

    print(f"  Token received ({len(token)} chars) — injecting…", flush=True)
    inject_hcaptcha_token(page, token)
    return True
