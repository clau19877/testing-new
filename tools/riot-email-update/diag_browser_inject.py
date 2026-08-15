#!/usr/bin/env python3
"""
Diagnostic: in-browser enterprise token inject for Riot's INVISIBLE hCaptcha.

The untested architecture:
  1. Real stealth browser (Patchright) + residential proxy
  2. Hook window.hcaptcha.render/execute BEFORE Riot's scripts run
  3. Load login, fill creds — capture the rqdata Riot passes into render()
  4. Mint a token with a vendor (Capless/NopeCHA/etc) using the SAME proxy +
     the browser's own User-Agent + that rqdata
  5. Arm the hook (window.__riotInjectToken) and click sign-in so Riot's own
     execute()/callback resolves with our token and RIOT'S client submits it
  6. Capture Riot's /api/v1/login verdict from the live session

This differs from probe_* scripts: the submit happens through Riot's real
browser session (cookies, TLS, client fingerprint), not a raw requests PUT.

Usage:
  DISPLAY=:99 PROXY_INDEX=40 INJECT_PROVIDER=capless \
    .venv/bin/python diag_browser_inject.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("NONINTERACTIVE", "1")

from captcha import (  # noqa: E402
    CaptchaSolverError,
    hook_execute_count,
    install_hcaptcha_hook,
    install_rqdata_network_capture,
    read_hook_render_config,
    set_inject_token,
    solve_capless_full,
)
from captcha_providers import proxy_egress_ip, solve_with_provider  # noqa: E402
from proxyutil import load_proxy_list, to_http_url  # noqa: E402
from stealth_browser import browser_engine, launch_stealth_browser  # noqa: E402


def log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


def main() -> int:
    user = os.getenv("RIOT_USERNAME") or ""
    password = os.getenv("RIOT_PASSWORD") or ""
    if not user or not password:
        log("init", "missing Riot creds")
        return 1

    proxies = load_proxy_list(os.getenv("PROXY_LIST") or "data/proxies.txt")
    idx = int(os.getenv("PROXY_INDEX") or "0")
    proxy = proxies[idx % len(proxies)] if proxies else (
        os.getenv("BROWSER_PROXY") or os.getenv("CAPLESS_PROXY") or ""
    )
    if not proxy:
        log("init", "missing proxy")
        return 1

    provider = (os.getenv("INJECT_PROVIDER") or "capless").strip().lower()
    keymap = {
        "capless": os.getenv("CAPLESS_API_KEY"),
        "nopecha": os.getenv("NOPECHA_API_KEY") or os.getenv("NOPECHA_KEY"),
        "twocaptcha": os.getenv("TWOCAPTCHA_API_KEY") or os.getenv("TWO_CAPTCHA_API_KEY"),
        "nonecap": os.getenv("NONECAP_API_KEY"),
    }
    api_key = (keymap.get(provider) or "").strip()
    if not api_key:
        log("init", f"no API key for provider={provider}")
        return 1

    report: dict = {
        "provider": provider,
        "proxy_index": idx,
        "engine": browser_engine(),
        "steps": [],
    }
    egress = proxy_egress_ip(proxy)
    report["egress_ip"] = egress
    log("init", f"provider={provider} engine={browser_engine()} egress={egress}")

    started = time.time()
    with launch_stealth_browser(headed=True, proxy=proxy) as (_p, _browser, context):
        # Hook must be installed at context level so it runs before page scripts.
        install_hcaptcha_hook(context)
        page = context.new_page()
        page.set_default_timeout(90_000)
        rq_sink = install_rqdata_network_capture(page)

        log("01", "open account.riotgames.com")
        page.goto("https://account.riotgames.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        report["url_open"] = page.url

        # cookies
        for sel in (
            'button[aria-label="Close this dialog"]',
            'button.osano-cm-dialog__close',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=1500)
                    page.wait_for_timeout(300)
            except Exception:
                pass

        log("02", "fill credentials")
        try:
            page.locator('input[name="username"]').first.fill(user)
            page.locator('input[name="password"]').first.fill(password)
        except Exception as exc:
            log("02", f"fill failed: {exc}")

        # Give Riot a moment to render the invisible widget (captures rqdata)
        page.wait_for_timeout(1500)
        render_cfg = read_hook_render_config(page)
        log("03", f"hook render config: {render_cfg}")
        report["render_config"] = render_cfg

        ua = page.evaluate("() => navigator.userAgent")
        report["browser_ua"] = ua

        # Resolve rqdata: prefer hook render config, then network capture.
        rqdata = None
        sitekey = "019f1553-3845-481c-a6f5-5a60ccf6d830"
        if render_cfg:
            rqdata = render_cfg.get("rqdata")
            sitekey = render_cfg.get("sitekey") or sitekey
        if not rqdata and rq_sink:
            rqdata = rq_sink[-1].get("rqdata")
            sitekey = rq_sink[-1].get("sitekey") or sitekey
        if not rqdata:
            try:
                rqdata = page.evaluate("() => window.__RIOT_RQDATA || null")
            except Exception:
                rqdata = None
        report["rqdata_present"] = bool(rqdata)
        report["rqdata_len"] = len(rqdata or "")
        log("03", f"rqdata_present={bool(rqdata)} len={len(rqdata or '')} sitekey={sitekey}")

        # Watch Riot's own submit verdict
        login_results: list[dict] = []

        def _on_resp(resp):
            try:
                u = (resp.url or "")
                if "/api/v1/login" in u and resp.request.method in ("PUT", "POST"):
                    try:
                        body = resp.json()
                    except Exception:
                        body = {"raw": (resp.text() or "")[:200]}
                    login_results.append(
                        {"method": resp.request.method, "status": resp.status,
                         "type": body.get("type"), "error": body.get("error"),
                         "has_captcha": bool(body.get("captcha"))}
                    )
            except Exception:
                pass

        page.on("response", _on_resp)

        # Mint token with vendor (same proxy + browser UA + captured rqdata)
        site = "https://authenticate.riotgames.com/"
        log("04", f"minting token via {provider} (proxy+UA+rqdata matched)…")
        try:
            t0 = time.time()
            if provider == "capless":
                token, ret_ua = solve_capless_full(
                    api_key, website_url=site, website_key=sitekey,
                    proxy=proxy, rqdata=rqdata,
                    timeout=float(os.getenv("CAPTCHA_TIMEOUT") or "180"),
                )
            else:
                token = solve_with_provider(
                    provider, api_key, website_url=site, website_key=sitekey,
                    rqdata=rqdata, user_agent=ua, proxy=proxy,
                    timeout=float(os.getenv("CAPTCHA_TIMEOUT") or "180"),
                    require_proxy=True, require_rqdata=bool(rqdata),
                )
            report["solve_s"] = round(time.time() - t0, 1)
            report["token_prefix"] = token[:12]
            report["token_len"] = len(token)
            log("04", f"token ok {token[:12]}… len={len(token)} in {report['solve_s']}s")
        except CaptchaSolverError as exc:
            report["solve_error"] = str(exc)[:300]
            log("04", f"SOLVE FAIL: {exc}")
            _write(report)
            return 2

        # Arm the hook and let Riot's client submit
        set_inject_token(page, token)
        log("05", "armed hook; clicking sign-in so Riot submits our token…")
        for sel in (
            'button[data-testid="btn-signin-submit"]',
            'button[type="submit"]',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=3000)
                    log("05", f"clicked {sel}")
                    break
            except Exception as exc:
                log("05", f"{sel} fail: {exc}")

        page.wait_for_timeout(6000)
        report["execute_calls"] = hook_execute_count(page)
        report["url_after"] = page.url
        report["login_results"] = login_results
        log("06", f"execute_calls={report['execute_calls']} url={page.url[:80]}")
        log("06", f"riot login verdicts: {login_results}")

        # Success heuristics
        logged_in = (
            "account.riotgames.com" in page.url
            and "authenticate" not in page.url
        )
        accepted = any(
            r.get("type") in ("success", "multifactor") or
            (r.get("status") == 200 and not r.get("error"))
            for r in login_results
        )
        report["logged_in"] = logged_in
        report["accepted"] = bool(accepted or logged_in)
        report["elapsed_s"] = round(time.time() - started, 1)
        try:
            page.screenshot(path=str(ROOT / "debug" / "diag_inject_final.png"))
        except Exception:
            pass

    _write(report)
    log("done", json.dumps({k: v for k, v in report.items() if k != "steps"}, indent=2))
    return 0 if report.get("accepted") else 2


def _write(report: dict) -> None:
    out = ROOT / "debug" / "diag_browser_inject_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"Wrote {out}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
