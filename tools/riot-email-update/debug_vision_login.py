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

    from vision_captcha import page_has_riot_oops, page_looks_mfa

    ensure_debug()
    report: dict = {"steps": [], "ok": False, "attempts": []}
    started = time.time()

    user = os.getenv("RIOT_USERNAME") or ""
    password = os.getenv("RIOT_PASSWORD") or ""
    new_email = os.getenv("NEW_EMAIL") or ""
    backend = os.getenv("VISION_BACKEND") or "hybrid"
    proxy_list = load_proxy_list(os.getenv("PROXY_LIST") or "data/proxies.txt")
    proxy_idx = int(os.getenv("PROXY_INDEX") or "0")
    max_attempts = int(os.getenv("PROXY_ATTEMPTS") or "3")

    log("init", f"user={user}")
    log("init", f"vision_backend={backend}")
    log("init", f"proxy_idx={proxy_idx} attempts={max_attempts} n_proxies={len(proxy_list)}")

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
    if not proxy_list and not (os.getenv("BROWSER_PROXY") or os.getenv("CAPLESS_PROXY")):
        log("init", "missing proxy")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )

        for attempt in range(max_attempts):
            idx = (proxy_idx + attempt) % len(proxy_list) if proxy_list else attempt
            proxy = (
                proxy_list[idx]
                if proxy_list
                else (os.getenv("BROWSER_PROXY") or os.getenv("CAPLESS_PROXY") or "")
            )
            attempt_note = {
                "attempt": attempt + 1,
                "proxy_index": idx,
                "session": (
                    proxy.split("session-")[1][:16]
                    if "session-" in proxy
                    else proxy[:40]
                ),
            }
            log(
                "session",
                f"attempt {attempt + 1}/{max_attempts} proxy_idx={idx} "
                f"session={attempt_note['session']}",
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
            attempt_ok = False

            try:
                # --- 01 open ---
                log("01", "opening account.riotgames.com")
                page.goto(
                    "https://account.riotgames.com/", wait_until="domcontentloaded"
                )
                page.wait_for_timeout(4000)
                log("01", f"url={page.url}")
                shot(page, f"a{attempt+1}_01_open")
                attempt_note["01_open"] = page.url

                dismiss_cookies(page)

                # --- 02 fill ---
                log("02", "filling credentials")
                page.locator('input[name="username"]').first.fill(user)
                page.locator('input[name="password"]').first.fill(password)
                shot(page, f"a{attempt+1}_02_filled")

                # --- 03 sign-in ---
                log("03", "clicking sign-in")
                click_signin(page)
                page.wait_for_timeout(5000)
                for _ in range(10):
                    if captcha_visible(page) or page_has_riot_oops(page):
                        break
                    page.wait_for_timeout(500)
                shot(page, f"a{attempt+1}_03_after_signin")
                log("03", f"captcha_visible={captcha_visible(page)}")
                if page_has_riot_oops(page):
                    log("03", "Oops after sign-in — rotate proxy")
                    attempt_note["status"] = "oops_signin"
                    context.close()
                    continue

                # --- 04 vision ---
                vision_ok = False
                if captcha_visible(page) or "authenticate.riotgames.com" in page.url:
                    log("04", "starting vision captcha solver")
                    vision_ok = solve_visible_captcha(
                        page, backend=backend, max_rounds=5
                    )
                    log("04", f"vision result={vision_ok}")
                    shot(page, f"a{attempt+1}_04_after_vision")
                    attempt_note["vision"] = vision_ok
                    if page_has_riot_oops(page):
                        log("04", "Oops after vision — rotate proxy")
                        attempt_note["status"] = "oops_vision"
                        context.close()
                        continue
                    if not vision_ok and captcha_visible(page):
                        log("04", "vision failed — rotate proxy")
                        attempt_note["status"] = "captcha_fail"
                        context.close()
                        continue
                else:
                    log("04", "no captcha after sign-in")
                    attempt_note["vision"] = "skipped"

                # --- 05 MFA ---
                page.wait_for_timeout(2500)
                shot(page, f"a{attempt+1}_05_pre_mfa")
                if page_has_riot_oops(page):
                    attempt_note["status"] = "oops_post"
                    context.close()
                    continue

                needs_mfa = (
                    inbox
                    and not page_logged_in(page)
                    and (
                        page_looks_mfa(page)
                        or page.locator(
                            'input[autocomplete="one-time-code"]'
                        ).count()
                        > 0
                    )
                )
                if needs_mfa:
                    log("05", "MFA UI detected — waiting IMAP")
                    try:
                        code = inbox.wait_for_code(
                            since_epoch=started - 10, timeout=150
                        )
                        log("05", f"got code {code}")
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
                                    log("05", f"filled via {sel}")
                                    break
                            except Exception:
                                continue
                        click_signin(page)
                        page.wait_for_timeout(4000)
                        attempt_note["mfa"] = "ok"
                    except Exception as exc:
                        log("05", f"MFA FAIL {exc}")
                        attempt_note["mfa"] = f"fail:{exc}"
                else:
                    log("05", "MFA skipped (not shown or no inbox)")
                    attempt_note["mfa"] = "skipped"

                # --- 06 ---
                log("06", f"final url={page.url}")
                shot(page, f"a{attempt+1}_06_final")
                if page_logged_in(page):
                    log("06", "LOGIN SUCCESS")
                    report["ok"] = True
                    attempt_note["status"] = "success"
                    attempt_ok = True
                    if new_email:
                        log("06", f"attempt email update UI → {new_email}")
                        page.goto(
                            "https://account.riotgames.com/",
                            wait_until="domcontentloaded",
                        )
                        page.wait_for_timeout(4000)
                        shot(page, f"a{attempt+1}_06_account")
                else:
                    log("06", "LOGIN NOT COMPLETE")
                    attempt_note["status"] = "incomplete"
                    try:
                        html = page.content()
                        (DEBUG_DIR / f"a{attempt+1}_06_final.html").write_text(html)
                    except Exception:
                        pass
            except Exception as exc:
                log("session", f"FAIL {exc}")
                attempt_note["status"] = f"error:{exc}"
                shot(page, f"a{attempt+1}_error")
            finally:
                report["attempts"].append(attempt_note)
                if not attempt_ok:
                    try:
                        context.close()
                    except Exception:
                        pass

            if attempt_ok:
                context.close()
                break

        report["elapsed_s"] = round(time.time() - started, 1)
        report["steps"].append({"attempts": report["attempts"]})
        (DEBUG_DIR / "debug_report.json").write_text(json.dumps(report, indent=2))
        log("done", json.dumps(report, indent=2))
        browser.close()
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
