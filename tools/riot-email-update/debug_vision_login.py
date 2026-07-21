#!/usr/bin/env python3
"""
Full-debug Riot login using in-browser vision captcha clicking.

Steps logged + screenshots under debug/:
  01 open portal
  02 fill credentials
  03 click sign-in
  04 vision-solve captcha rounds
  05 post-captcha / MFA via IMAP
  06 account page / email update attempt
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("NONINTERACTIVE", "1")

from imap_mail import ImapConfig, ImapInbox
from proxyutil import load_proxy_list, to_playwright
from vision_captcha import (
    DEBUG_DIR,
    captcha_visible,
    ensure_debug,
    solve_visible_captcha,
)


def log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


def shot(page, name: str) -> Path:
    ensure_debug()
    path = DEBUG_DIR / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
        log("shot", path.name)
    except Exception as exc:
        log("shot", f"FAILED {name}: {exc}")
    return path


def dismiss_cookies(page) -> None:
    for sel in [
        'button[aria-label="Close this dialog"]',
        'button:has-text("Accept")',
        'button:has-text("Deny Non-Essential")',
        'button.osano-cm-dialog__close',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count():
                loc.click(timeout=1500)
                log("cookies", f"clicked {sel}")
                page.wait_for_timeout(500)
        except Exception:
            pass


def click_signin(page) -> bool:
    dismiss_cookies(page)
    for sel in [
        'button[data-testid="btn-signin-submit"]',
        'button[type="submit"]',
        'button:has(svg)',
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count():
                loc.click(timeout=3000)
                log("signin", f"clicked {sel}")
                return True
        except Exception as exc:
            log("signin", f"{sel} fail: {exc}")
    clicked = page.evaluate(
        """() => {
          const buttons=[...document.querySelectorAll('button')];
          const c=buttons.find(b=>{
            const r=b.getBoundingClientRect();
            return r.width>40&&r.width<120&&r.height>40&&r.height<120&&r.top>200;
          });
          if(!c) return false; c.click(); return true;
        }"""
    )
    log("signin", f"circular heuristic={clicked}")
    return bool(clicked)


def page_logged_in(page) -> bool:
    u = page.url
    return (
        "account.riotgames.com" in u
        and "authenticate" not in u
        and "auth.riotgames.com" not in u
    )


def main() -> int:
    from playwright.sync_api import sync_playwright

    ensure_debug()
    report: dict = {"steps": [], "ok": False}
    started = time.time()

    user = os.getenv("RIOT_USERNAME") or ""
    password = os.getenv("RIOT_PASSWORD") or ""
    new_email = os.getenv("NEW_EMAIL") or ""
    backend = os.getenv("VISION_BACKEND") or "auto"
    proxy_list = load_proxy_list(os.getenv("PROXY_LIST") or "data/proxies.txt")
    proxy_idx = int(os.getenv("PROXY_INDEX") or "9")
    proxy = (
        proxy_list[proxy_idx % len(proxy_list)]
        if proxy_list
        else (os.getenv("BROWSER_PROXY") or os.getenv("CAPLESS_PROXY") or "")
    )

    log("init", f"user={user}")
    log("init", f"vision_backend={backend}")
    log("init", f"proxy_idx={proxy_idx} session={(proxy.split('session-')[1][:16] if 'session-' in proxy else proxy[:40])}")

    cfg = ImapConfig.from_env(dict(os.environ), "IMAP")
    inbox = None
    if cfg:
        try:
            inbox = ImapInbox(cfg)
            inbox.test_connection()
            log("imap", f"OK {cfg.user}")
            report["steps"].append({"imap": "ok"})
        except Exception as exc:
            log("imap", f"FAIL {exc}")
            report["steps"].append({"imap": f"fail:{exc}"})

    if not user or not password:
        log("init", "missing Riot creds")
        return 1
    if not proxy:
        log("init", "missing proxy")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
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

        # --- 01 open ---
        log("01", "opening account.riotgames.com")
        try:
            page.goto("https://account.riotgames.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            log("01", f"url={page.url}")
            log("01", f"title={page.title()}")
            shot(page, "01_open")
            report["steps"].append({"01_open": "ok", "url": page.url})
        except Exception as exc:
            log("01", f"FAIL {exc}")
            shot(page, "01_open_fail")
            report["steps"].append({"01_open": f"fail:{exc}"})
            (DEBUG_DIR / "debug_report.json").write_text(json.dumps(report, indent=2))
            context.close()
            browser.close()
            return 1

        dismiss_cookies(page)

        # --- 02 fill ---
        log("02", "filling credentials")
        try:
            page.locator('input[name="username"]').first.fill(user)
            page.locator('input[name="password"]').first.fill(password)
            shot(page, "02_filled")
            report["steps"].append({"02_fill": "ok"})
        except Exception as exc:
            log("02", f"FAIL {exc}")
            shot(page, "02_fill_fail")
            report["steps"].append({"02_fill": f"fail:{exc}"})

        # --- 03 sign-in ---
        log("03", "clicking sign-in")
        click_signin(page)
        page.wait_for_timeout(5000)
        # Wait briefly for hCaptcha challenge to mount
        for _ in range(10):
            if captcha_visible(page):
                break
            page.wait_for_timeout(500)
        shot(page, "03_after_signin")
        log("03", f"url={page.url}")
        log("03", f"captcha_visible={captcha_visible(page)}")
        report["steps"].append(
            {"03_signin": "ok", "url": page.url, "captcha": captcha_visible(page)}
        )

        # --- 04 vision captcha ---
        if captcha_visible(page) or "authenticate.riotgames.com" in page.url:
            log("04", "starting vision captcha solver")
            try:
                ok = solve_visible_captcha(page, backend=backend, max_rounds=5)
                log("04", f"vision result={ok}")
                shot(page, "04_after_vision")
                report["steps"].append({"04_vision": "ok" if ok else "fail", "result": ok})
            except Exception as exc:
                log("04", f"FAIL {exc}")
                shot(page, "04_vision_fail")
                report["steps"].append({"04_vision": f"fail:{exc}"})

            # If still on login, try sign-in again after captcha (not on Oops)
            content_l = ""
            try:
                content_l = page.content().lower()
            except Exception:
                pass
            if "something went wrong" in content_l:
                log("04b", "Riot Oops after vision — not re-signing in")
                report["steps"].append({"04b_oops": True})
            elif not page_logged_in(page):
                log("04b", "re-click sign-in after vision")
                click_signin(page)
                page.wait_for_timeout(4000)
                shot(page, "04b_resignin")
                # maybe captcha again
                if captcha_visible(page):
                    log("04c", "captcha still present — second vision pass")
                    try:
                        ok2 = solve_visible_captcha(page, backend=backend, max_rounds=3)
                        report["steps"].append({"04c_vision2": ok2})
                    except Exception as exc:
                        report["steps"].append({"04c_vision2": f"fail:{exc}"})
                    click_signin(page)
                    page.wait_for_timeout(3000)
                shot(page, "04c_after")
        else:
            log("04", "no captcha detected after sign-in")
            report["steps"].append({"04_vision": "skipped_no_captcha"})

        log("05", f"url after captcha flow: {page.url}")
        shot(page, "05_pre_mfa")

        # --- 05 MFA ---
        content_l = ""
        try:
            content_l = page.content().lower()
        except Exception:
            pass
        mfa_input = (
            page.locator('input[autocomplete="one-time-code"]').count() > 0
            or page.locator('input[inputmode="numeric"]').count() > 0
            or page.locator('input[name*="code" i]').count() > 0
        )
        needs_mfa = (
            inbox
            and not page_logged_in(page)
            and not captcha_visible(page)
            and mfa_input
            and (
                "multifactor" in content_l
                or "enter the code" in content_l
                or "verification code" in content_l
                or "check your email" in content_l
            )
        )
        if needs_mfa:
            log("05", "MFA UI detected — waiting IMAP")
            try:
                code = inbox.wait_for_code(since_epoch=started - 10, timeout=150)
                log("05", f"got code {code}")
                filled = False
                for sel in [
                    'input[autocomplete="one-time-code"]',
                    'input[name*="code" i]',
                    'input[inputmode="numeric"]',
                    'input[type="tel"]',
                    'input[type="text"]',
                ]:
                    loc = page.locator(sel).first
                    try:
                        if loc.count():
                            loc.fill(code)
                            filled = True
                            log("05", f"filled via {sel}")
                            break
                    except Exception:
                        continue
                if filled:
                    click_signin(page)
                    page.wait_for_timeout(4000)
                report["steps"].append({"05_mfa": "ok", "code": code})
            except Exception as exc:
                log("05", f"MFA FAIL {exc}")
                report["steps"].append({"05_mfa": f"fail:{exc}"})
            shot(page, "05_after_mfa")
        else:
            log("05", "MFA skipped (not shown or no inbox)")
            report["steps"].append({"05_mfa": "skipped"})

        # --- 06 account ---
        log("06", f"final url={page.url}")
        shot(page, "06_final")
        if page_logged_in(page):
            log("06", "LOGIN SUCCESS")
            report["ok"] = True
            report["steps"].append({"06_login": "success"})
            if new_email:
                log("06", f"attempt email update UI → {new_email}")
                page.goto("https://account.riotgames.com/", wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                shot(page, "06_account")
                report["steps"].append({"06_account": page.url})
        else:
            log("06", "LOGIN NOT COMPLETE")
            # dump HTML snip for debug
            try:
                html = page.content()
                (DEBUG_DIR / "06_final.html").write_text(html)
                log("06", f"saved HTML ({len(html)} bytes)")
            except Exception as exc:
                log("06", f"html dump fail: {exc}")
            report["steps"].append({"06_login": "fail", "url": page.url})

        report["elapsed_s"] = round(time.time() - started, 1)
        (DEBUG_DIR / "debug_report.json").write_text(json.dumps(report, indent=2))
        log("done", json.dumps(report, indent=2))

        context.close()
        browser.close()
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
