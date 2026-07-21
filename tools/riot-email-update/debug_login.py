#!/usr/bin/env python3
"""Debug/test runner for Riot login + CapSolver + IMAP (non-interactive)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

os.environ.setdefault("NONINTERACTIVE", "1")

from captcha import CapSolverError, extract_hcaptcha_params, known_sitekey_for_url, solve_and_inject
from imap_mail import ImapConfig, ImapInbox


def log(msg: str) -> None:
    print(msg, flush=True)


def shot(page, name: str) -> None:
    out = Path(__file__).resolve().parent / "debug"
    out.mkdir(exist_ok=True)
    path = out / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        log(f"  screenshot → {path}")
    except Exception as exc:
        log(f"  screenshot failed: {exc}")


def main() -> int:
    from playwright.sync_api import sync_playwright

    user = os.getenv("RIOT_USERNAME") or ""
    password = os.getenv("RIOT_PASSWORD") or ""
    new_email = os.getenv("NEW_EMAIL") or ""
    key = os.getenv("CAPSOLVER_API_KEY") or ""
    proxy = (os.getenv("CAPSOLVER_PROXY") or "").strip() or None

    if not user or not password:
        log("Missing RIOT_USERNAME / RIOT_PASSWORD")
        return 1

    cfg = ImapConfig.from_env(dict(os.environ), "IMAP")
    inbox = ImapInbox(cfg) if cfg else None
    if inbox:
        try:
            inbox.test_connection()
            log(f"IMAP OK: {cfg.user}")
        except Exception as exc:
            log(f"IMAP FAIL: {exc}")
            inbox = None

    headed = (os.getenv("HEADED") or "true").lower() in {"1", "true", "yes"}
    log(f"Launching chromium headed={headed}")
    login_started = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.set_default_timeout(60_000)

        log("GOTO account.riotgames.com")
        page.goto("https://account.riotgames.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        log(f"URL: {page.url}")
        log(f"TITLE: {page.title()}")
        shot(page, "01_landing")

        # fill login
        for sel in [
            'input[name="username"]',
            'input[type="text"]',
            'input[type="email"]',
            "#username",
        ]:
            loc = page.locator(sel).first
            if loc.count():
                try:
                    loc.fill(user)
                    log(f"filled username via {sel}")
                    break
                except Exception:
                    continue
        else:
            log("USERNAME FIELD NOT FOUND")
            shot(page, "01b_no_user")

        for sel in ['input[name="password"]', 'input[type="password"]', "#password"]:
            loc = page.locator(sel).first
            if loc.count():
                try:
                    loc.fill(password)
                    log(f"filled password via {sel}")
                    break
                except Exception:
                    continue
        else:
            log("PASSWORD FIELD NOT FOUND")

        shot(page, "02_filled")
        params = extract_hcaptcha_params(page)
        log(f"hcaptcha params pre-submit: {params}")
        sitekey = params.get("sitekey") or known_sitekey_for_url(page.url)
        log(f"sitekey resolved: {sitekey}")

        if key:
            log("Solving captcha with CapSolver (pre-submit)…")
            try:
                ok = solve_and_inject(page, key, proxy=proxy)
                log(f"CapSolver inject: {ok}")
            except CapSolverError as exc:
                log(f"CapSolver error: {exc}")
            except Exception as exc:
                log(f"CapSolver unexpected: {exc}")

        shot(page, "03_after_captcha")

        clicked = False
        for sel in [
            'button[type="submit"]',
            'button:has-text("Sign in")',
            'button:has-text("Sign In")',
            'button:has-text("Log in")',
        ]:
            loc = page.locator(sel).first
            if loc.count():
                try:
                    loc.click()
                    log(f"clicked {sel}")
                    clicked = True
                    break
                except Exception as exc:
                    log(f"click fail {sel}: {exc}")
        if not clicked:
            log("NO SUBMIT BUTTON")

        page.wait_for_timeout(5000)
        log(f"URL after submit: {page.url}")
        shot(page, "04_after_submit")

        # post-submit captcha?
        if "account.riotgames.com" not in page.url or "login" in page.url.lower() or "auth" in page.url:
            params = extract_hcaptcha_params(page)
            log(f"hcaptcha params post-submit: {params}")
            if key:
                log("Solving captcha with CapSolver (post-submit)…")
                try:
                    ok = solve_and_inject(page, key, proxy=proxy)
                    log(f"CapSolver inject: {ok}")
                    for sel in ['button[type="submit"]', 'button:has-text("Sign in")']:
                        loc = page.locator(sel).first
                        if loc.count():
                            try:
                                loc.click()
                                log(f"re-clicked {sel}")
                                break
                            except Exception:
                                pass
                    page.wait_for_timeout(5000)
                except Exception as exc:
                    log(f"post captcha fail: {exc}")
            shot(page, "05_post_captcha")

        log(f"URL now: {page.url}")
        content_snip = page.content()[:1500].replace("\n", " ")
        log(f"HTML snip: {content_snip}")

        # MFA via IMAP?
        code_sel = page.locator(
            'input[autocomplete="one-time-code"], input[name*="code" i], input[inputmode="numeric"]'
        )
        if inbox and (code_sel.count() > 0 or "code" in page.content().lower()):
            log("MFA-like page detected; waiting IMAP code…")
            try:
                code = inbox.wait_for_code(since_epoch=login_started - 10, timeout=120)
                log(f"Got code: {code}")
                if code_sel.count():
                    code_sel.first.fill(code)
                else:
                    page.locator('input[type="text"], input[type="tel"]').first.fill(code)
                for sel in ['button[type="submit"]', 'button:has-text("Submit")', 'button:has-text("Continue")']:
                    loc = page.locator(sel).first
                    if loc.count():
                        loc.click()
                        break
                page.wait_for_timeout(5000)
            except Exception as exc:
                log(f"IMAP MFA failed: {exc}")
            shot(page, "06_after_mfa")

        log(f"FINAL URL: {page.url}")
        shot(page, "07_final")

        # If logged in and new email set, try update briefly
        if new_email and "account.riotgames.com" in page.url and "auth" not in page.url:
            log(f"Attempting email update → {new_email}")
            page.goto("https://account.riotgames.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            shot(page, "08_account")
            log(f"account page URL: {page.url}")
            log(f"account title: {page.title()}")

        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
