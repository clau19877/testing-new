"""CapSolver client for Riot hCaptcha (enterprise / rqdata aware)."""

from __future__ import annotations

import time
from typing import Any

import requests

CAPSOLVER_CREATE = "https://api.capsolver.com/createTask"
CAPSOLVER_RESULT = "https://api.capsolver.com/getTaskResult"

# Known Riot hCaptcha sitekeys (used if the live page does not expose one).
RIOT_SITEKEYS = {
    "authenticate.riotgames.com": "019f1553-3845-481c-a6f5-5a60ccf6d830",
    "auth.riotgames.com": "019f1553-3845-481c-a6f5-5a60ccf6d830",
    "account.riotgames.com": "b3c619fc-4b72-4838-9bf2-398888588d62",
    "recovery.riotgames.com": "db18a187-7b77-4dac-a6cb-6dd5215973cf",
}


class CapSolverError(RuntimeError):
    pass


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

            // iframe src often embeds sitekey
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

            // Common globals / config blobs on Riot auth pages
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


def solve_hcaptcha(
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
    """
    Create a CapSolver hCaptcha task and poll until a token is ready.

    Prefer HCaptchaTaskProxyLess unless PROXY is configured (HCaptchaTask).
    """
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
        # CapSolver accepts http:ip:port:user:pass or http://user:pass@ip:port
        task["proxy"] = proxy

    create = requests.post(
        CAPSOLVER_CREATE,
        json={"clientKey": api_key, "task": task},
        timeout=60,
    )
    create.raise_for_status()
    created = create.json()
    if created.get("errorId"):
        raise CapSolverError(
            f"createTask failed: {created.get('errorCode')} "
            f"{created.get('errorDescription')}"
        )

    task_id = created.get("taskId")
    if not task_id:
        raise CapSolverError(f"createTask returned no taskId: {created}")

    print(f"  CapSolver task created: {task_id}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        result = requests.post(
            CAPSOLVER_RESULT,
            json={"clientKey": api_key, "taskId": task_id},
            timeout=60,
        )
        result.raise_for_status()
        body = result.json()
        if body.get("errorId"):
            raise CapSolverError(
                f"getTaskResult failed: {body.get('errorCode')} "
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
                raise CapSolverError(f"ready but no token: {body}")
            return token
        if status == "failed":
            raise CapSolverError(f"solve failed: {body}")
        print(f"  CapSolver status: {status or 'processing'}…")

    raise CapSolverError(f"timed out after {timeout:.0f}s waiting for CapSolver")


def inject_hcaptcha_token(page, token: str) -> None:
    """Write the solved token into the page and notify hCaptcha callbacks if present."""
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

            // Ensure fields exist if the widget has not rendered them yet
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

            // Prefer official callback path when available
            try {
              if (window.hcaptcha) {
                const clients = window.hcaptcha._original_hcaptcha
                  || window.hcaptcha;
                // best-effort: many Riot builds expose submit/callback hooks
                if (typeof window.hcaptcha.execute === 'function') {
                  // no-op; token already injected
                }
              }
            } catch (e) {}

            // Generic callback names sometimes attached to the widget
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

            // data-callback attribute on widget
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
    api_key: str,
    *,
    proxy: str | None = None,
) -> bool:
    """
    Detect hCaptcha on the current page, solve via CapSolver, inject token.
    Returns True if a token was injected, False if no captcha params found.
    """
    website_url = page.url
    user_agent = page.evaluate("() => navigator.userAgent")
    params = extract_hcaptcha_params(page)
    sitekey = params.get("sitekey") or known_sitekey_for_url(website_url)
    rqdata = params.get("rqdata")
    is_invisible = bool(params.get("isInvisible"))

    if not sitekey:
        print("  No hCaptcha sitekey found on page — skipping CapSolver.")
        return False

    print(f"  hCaptcha sitekey: {sitekey}")
    if rqdata:
        print(f"  rqdata present ({len(rqdata)} chars)")
    else:
        print("  No rqdata found (will try without enterprise payload)")

    token = solve_hcaptcha(
        api_key,
        website_url=website_url,
        website_key=sitekey,
        user_agent=user_agent,
        rqdata=rqdata,
        is_invisible=is_invisible,
        proxy=proxy,
    )
    print(f"  CapSolver token received ({len(token)} chars) — injecting…")
    inject_hcaptcha_token(page, token)
    return True
