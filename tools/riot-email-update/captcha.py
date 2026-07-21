"""Captcha solvers for Riot hCaptcha.

Riot uses **hCaptcha Enterprise** with a fresh per-challenge `rqdata` blob.
A valid token must be minted against that exact rqdata on an IP that matches
the submit session.

Approaches implemented in this package
--------------------------------------
Token APIs (captcha.py / captcha_providers.py):
  - capmonster  — HCaptchaTask + customData=rqdata (common in Riot auth scripts)
  - twocaptcha  — HCaptchaTask + data=rqdata + matching User-Agent
  - nonecap     — type=hcaptcha_enterprise + rqdata
  - capless     — /solve with site+sitekey+proxy+rqdata (lists Riot; often soft-fails)
  - capsolver   — hard-rejects Riot sitekey ("We don't support this service")

In-browser (vision_captcha.py):
  - OCR/CV letter-grid + drag heuristics, optional agent/OpenAI backends
  - Does NOT mint enterprise-bound tokens; Riot may still reject / Oops

Riot Client API path (riot_api_login.py):
  - POST auth.riotgames.com/authorization → POST authenticate …/login
  - Extract sitekey+rqdata → solver → PUT login with `hcaptcha <token>`
  - Preferred over injecting tokens into the web widget

Probe: `python probe_hcaptcha_approaches.py`
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
    Returns the captcha token string.
    """
    token, _ua = solve_capless_full(
        api_key,
        website_url=website_url,
        website_key=website_key,
        proxy=proxy,
        rqdata=rqdata,
        timeout=timeout,
    )
    return token


def solve_capless_full(
    api_key: str,
    *,
    website_url: str,
    website_key: str,
    proxy: str,
    rqdata: str | None = None,
    timeout: float = 180.0,
) -> tuple[str, str | None]:
    """Return (token, user_agent_from_capless)."""
    from proxyutil import to_http_url

    site = website_url
    proxy_fmt = to_http_url(proxy)

    headers = {"Content-Type": "application/json", "x-api-key": api_key}
    payload: dict[str, Any] = {
        "type": "hcaptcha",
        "site": site,
        "sitekey": website_key,
        "proxy": proxy_fmt,
    }
    if rqdata:
        payload["rqdata"] = rqdata

    print(f"  Capless: submitting solve via {proxy_fmt.split('@')[-1]}…", flush=True)
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
    return token, data.get("user_agent")


def inject_hcaptcha_token(page, token: str) -> None:
    """Write the solved token into the page without embedding it in JS source."""
    # Ensure response fields exist
    page.evaluate(
        """() => {
            for (const name of ['h-captcha-response', 'g-recaptcha-response']) {
              let el = document.querySelector('textarea[name=\"' + name + '\"], [name=\"' + name + '\"]');
              if (!el) {
                el = document.createElement('textarea');
                el.name = name;
                el.setAttribute('name', name);
                el.style.display = 'none';
                (document.getElementById('root') || document.body).appendChild(el);
              }
            }
        }"""
    )
    for name in ("h-captcha-response", "g-recaptcha-response"):
        loc = page.locator(f'textarea[name="{name}"]').first
        try:
            loc.fill(token)
        except Exception:
            page.evaluate(
                """({ name, token }) => {
                    const el = document.querySelector('textarea[name=\"' + name + '\"]');
                    if (el) {
                      el.value = token;
                      el.dispatchEvent(new Event('input', { bubbles: true }));
                      el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""",
                {"name": name, "token": token},
            )

    # Notify callbacks if present
    page.evaluate(
        """(token) => {
            const cbNames = ['onCaptchaSuccess','captchaCallback','hcaptchaCallback','onSuccess'];
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
            try {
              if (window.hcaptcha && typeof window.hcaptcha.getResponse === 'function') {
                // best-effort: some builds read from textarea only
              }
            } catch (e) {}
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
    provider: capless | capsolver | capmonster | twocaptcha | nonecap
    """
    import os

    from captcha_providers import solve_with_provider

    website_url = page.url
    # Capless allowlist is host/* — keep origin+path, drop huge query if needed
    if "authenticate.riotgames.com" in website_url:
        website_url = website_url.split("#")[0]
        if len(website_url) > 300:
            website_url = "https://authenticate.riotgames.com/"
    user_agent = page.evaluate("() => navigator.userAgent")
    params = extract_hcaptcha_params(page)
    sitekey = params.get("sitekey") or known_sitekey_for_url(page.url)
    rqdata = params.get("rqdata")

    # Try to pull fresh rqdata from recent performance entries / page globals
    if not rqdata:
        try:
            rqdata = page.evaluate(
                """() => {
                  try {
                    if (window.__RIOT_RQDATA) return window.__RIOT_RQDATA;
                    const html = document.documentElement.innerHTML;
                    const m = html.match(/\"rqdata\"\\s*:\\s*\"([^\"]+)\"/);
                    return m ? m[1] : null;
                  } catch (e) { return null; }
                }"""
            )
        except Exception:
            rqdata = None

    if not sitekey:
        print("  No hCaptcha sitekey found on page — skipping solver.", flush=True)
        return False

    print(f"  hCaptcha sitekey: {sitekey}", flush=True)
    if rqdata:
        print(f"  rqdata present ({len(rqdata)} chars)", flush=True)
    else:
        print("  No rqdata found (will try without enterprise payload)", flush=True)

    provider = (provider or "capless").strip().lower()
    # CapMonster-style Riot scripts often use auth.riotgames.com as websiteURL
    if provider in ("capmonster", "twocaptcha", "2captcha", "nonecap"):
        website_url = os.getenv("HCAPTCHA_WEBSITE_URL") or "https://auth.riotgames.com"

    if provider == "capless" and not proxy:
        raise CaptchaSolverError(
            "Capless requires a proxy (set CAPLESS_PROXY / CAPTCHA_PROXY). "
            "Use a residential proxy, ideally same IP as this browser."
        )

    token = solve_with_provider(
        provider,
        api_key,
        website_url=website_url,
        website_key=sitekey,
        rqdata=rqdata,
        user_agent=user_agent,
        proxy=proxy,
    )

    print(f"  Token received ({len(token)} chars) — injecting…", flush=True)
    inject_hcaptcha_token(page, token)
    return True
