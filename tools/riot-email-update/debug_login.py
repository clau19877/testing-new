#!/usr/bin/env python3
"""Debug/test runner: Riot login via Capless + matching browser proxy + IMAP."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
os.environ.setdefault("NONINTERACTIVE", "1")

from captcha import (
    CaptchaSolverError,
    extract_hcaptcha_params,
    install_rqdata_network_capture,
    known_sitekey_for_url,
    solve_and_inject,
)
from imap_mail import ImapConfig, ImapInbox
from proxyutil import pick_proxy, to_playwright


def log(msg: str) -> None:
    print(msg, flush=True)


def shot(page, name: str) -> None:
    out = Path(__file__).resolve().parent / "debug"
    out.mkdir(exist_ok=True)
    path = out / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        log(f"  screenshot → {path.name}")
    except Exception as exc:
        log(f"  screenshot failed: {exc}")


def click_signin(page) -> bool:
    try:
        page.locator('button[aria-label="Close this dialog"]').first.click(timeout=2000)
        log("  dismissed cookie banner")
    except Exception:
        pass
    for sel in [
        'button[data-testid="btn-signin-submit"]',
        'button[type="submit"]',
        'button:has(svg)',
    ]:
        loc = page.locator(sel).first
        try:
            if loc.count():
                loc.click(timeout=3000)
                log(f"  clicked {sel}")
                return True
        except Exception:
            continue
    clicked = page.evaluate(
        """() => {
          const buttons = [...document.querySelectorAll('button')];
          const candidate = buttons.find(b => {
            const r = b.getBoundingClientRect();
            return r.width > 40 && r.width < 120 && r.height > 40 && r.height < 120 && r.top > 200;
          });
          if (!candidate) return false;
          candidate.click();
          return true;
        }"""
    )
    log(f"  circular click: {clicked}")
    return bool(clicked)


def main() -> int:
    from playwright.sync_api import sync_playwright

    user = os.getenv("RIOT_USERNAME") or ""
    password = os.getenv("RIOT_PASSWORD") or ""
    new_email = os.getenv("NEW_EMAIL") or ""
    provider = (os.getenv("CAPTCHA_PROVIDER") or "twocaptcha").lower()
    key = (
        os.getenv("CAPLESS_API_KEY")
        or os.getenv("CAPTCHA_API_KEY")
        or os.getenv("CAPSOLVER_API_KEY")
        or ""
    )
    proxy = pick_proxy(
        os.getenv("CAPLESS_PROXY") or os.getenv("BROWSER_PROXY"),
        os.getenv("PROXY_LIST") or "data/proxies.txt",
        index=0,
    )
    if not user or not password:
        log("Missing Riot creds")
        return 1
    if not key:
        log("Missing Capless API key")
        return 1
    if not proxy:
        log("Missing proxy")
        return 1

    cfg = ImapConfig.from_env(dict(os.environ), "IMAP")
    inbox = ImapInbox(cfg) if cfg else None
    if inbox:
        inbox.test_connection()
        log(f"IMAP OK: {cfg.user}")

    headed = (os.getenv("HEADED") or "true").lower() in {"1", "true", "yes"}
    login_started = time.time()
    log(f"provider={provider} proxy={proxy.split(':')[0]}… headed={headed}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            proxy=to_playwright(proxy),
        )
        page = context.new_page()
        page.set_default_timeout(90_000)
        install_rqdata_network_capture(page)

        log("GOTO account.riotgames.com")
        page.goto("https://account.riotgames.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        log(f"URL: {page.url}")
        log(f"TITLE: {page.title()}")
        shot(page, "01_landing")

        # verify egress IP via proxy in-page if possible
        try:
            ip = page.evaluate(
                """async () => {
                  try {
                    const r = await fetch('https://api.ipify.org?format=json');
                    const j = await r.json();
                    return j.ip;
                  } catch (e) { return String(e); }
                }"""
            )
            log(f"browser egress IP: {ip}")
        except Exception as exc:
            log(f"ip check failed: {exc}")

        for sel in ['input[name="username"]', 'input[type="text"]', "#username"]:
            loc = page.locator(sel).first
            if loc.count():
                try:
                    loc.fill(user)
                    log(f"filled username via {sel}")
                    break
                except Exception:
                    continue
        for sel in ['input[name="password"]', 'input[type="password"]']:
            loc = page.locator(sel).first
            if loc.count():
                try:
                    loc.fill(password)
                    log(f"filled password via {sel}")
                    break
                except Exception:
                    continue

        shot(page, "02_filled")
        params = extract_hcaptcha_params(page)
        log(f"hcaptcha params: {params}")
        sitekey = params.get("sitekey") or known_sitekey_for_url(page.url)
        log(f"sitekey: {sitekey}")

        # Capless solve + inject before submit
        try:
            ok = solve_and_inject(page, provider=provider, api_key=key, proxy=proxy)
            log(f"captcha inject: {ok}")
        except CaptchaSolverError as exc:
            log(f"captcha error: {exc}")
            # try next proxy session
            proxy2 = pick_proxy(None, os.getenv("PROXY_LIST") or "data/proxies.txt", index=1)
            if proxy2 and proxy2 != proxy:
                log(f"retry Capless with next proxy session…")
                try:
                    ok = solve_and_inject(page, provider=provider, api_key=key, proxy=proxy2)
                    log(f"captcha inject retry: {ok}")
                except Exception as exc2:
                    log(f"retry failed: {exc2}")
        except Exception as exc:
            log(f"captcha unexpected: {exc}")

        shot(page, "03_after_captcha")
        click_signin(page)
        page.wait_for_timeout(6000)
        log(f"URL after submit: {page.url}")
        shot(page, "04_after_submit")

        # If still on auth, try captcha again (sometimes appears after submit)
        if "authenticate.riotgames.com" in page.url or "auth.riotgames.com" in page.url:
            try:
                ok = solve_and_inject(page, provider=provider, api_key=key, proxy=proxy)
                log(f"post-submit captcha: {ok}")
                if ok:
                    click_signin(page)
                    page.wait_for_timeout(5000)
            except Exception as exc:
                log(f"post-submit captcha fail: {exc}")
            shot(page, "05_post_captcha")

        # MFA
        content_l = page.content().lower()
        if inbox and ("code" in content_l or page.locator('input[autocomplete="one-time-code"]').count()):
            log("MFA-like UI — waiting IMAP…")
            try:
                code = inbox.wait_for_code(since_epoch=login_started - 10, timeout=150)
                log(f"code={code}")
                for sel in [
                    'input[autocomplete="one-time-code"]',
                    'input[name*="code" i]',
                    'input[inputmode="numeric"]',
                    'input[type="tel"]',
                    'input[type="text"]',
                ]:
                    loc = page.locator(sel).first
                    if loc.count():
                        try:
                            loc.fill(code)
                            break
                        except Exception:
                            continue
                click_signin(page)
                page.wait_for_timeout(5000)
            except Exception as exc:
                log(f"IMAP MFA fail: {exc}")
            shot(page, "06_mfa")

        log(f"FINAL URL: {page.url}")
        shot(page, "07_final")

        if (
            new_email
            and "account.riotgames.com" in page.url
            and "authenticate" not in page.url
            and "auth.riotgames.com" not in page.url
        ):
            log(f"Logged in — opening account for email update → {new_email}")
            page.goto("https://account.riotgames.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            shot(page, "08_account")
            log(f"account URL={page.url} title={page.title()}")
        else:
            log("Not fully logged in — email update skipped")

        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
