#!/usr/bin/env python3
"""
Interactive helper: sign in to your Riot Games account and update its email.

- hCaptcha via CapSolver (CAPSOLVER_API_KEY)
- MFA / email verification codes via IMAP (IMAP_* / NEW_IMAP_*)

Usage:
  cd tools/riot-email-update
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  playwright install chromium
  cp .env.example .env
  python update_email.py
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from captcha import CapSolverError, solve_and_inject
from imap_mail import ImapConfig, ImapInbox

ACCOUNT_URL = "https://account.riotgames.com/"
AUTH_HOST_HINT = "auth.riotgames.com"


def prompt(label: str, *, secret: bool = False, default: str | None = None) -> str:
    hint = f" [{default}]" if default else ""
    while True:
        if secret:
            value = getpass.getpass(f"{label}{hint}: ").strip()
        else:
            value = input(f"{label}{hint}: ").strip()
        if not value and default is not None:
            value = default
        if value:
            return value
        print("  Value required.")


def pause(message: str) -> None:
    input(f"\n>>> {message}\n    Press Enter to continue… ")


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_map() -> dict[str, str]:
    return {k: (v if v is not None else "") for k, v in os.environ.items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log into your Riot account and update the email address."
    )
    parser.add_argument("--username", help="Riot username or email (or RIOT_USERNAME)")
    parser.add_argument("--new-email", help="New email address (or NEW_EMAIL)")
    parser.add_argument(
        "--headed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show the browser window (default: true / HEADED env)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=180_000,
        help="Default Playwright timeout in ms (default: 180000)",
    )
    parser.add_argument(
        "--capsolver-key",
        help="CapSolver API key (or CAPSOLVER_API_KEY)",
    )
    parser.add_argument(
        "--no-captcha-solver",
        action="store_true",
        help="Disable CapSolver even if an API key is configured",
    )
    parser.add_argument(
        "--imap-timeout",
        type=float,
        default=180.0,
        help="Seconds to wait for Riot mail via IMAP (default: 180)",
    )
    return parser.parse_args()


def fill_first_matching(page, selectors: list[str], value: str, field_name: str) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() == 0:
                continue
            locator.wait_for(state="visible", timeout=5_000)
            locator.fill(value)
            print(f"  Filled {field_name} via: {selector}")
            return True
        except Exception:
            continue
    return False


def click_first_matching(page, selectors: list[str], action_name: str) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() == 0:
                continue
            locator.wait_for(state="visible", timeout=5_000)
            locator.click()
            print(f"  Clicked {action_name} via: {selector}")
            return True
        except Exception:
            continue
    return False


def page_looks_logged_in(page) -> bool:
    href = page.url
    return (
        "account.riotgames.com" in href
        and "auth.riotgames.com" not in href
        and "authenticate.riotgames.com" not in href
        and "/login" not in href
    )


def wait_until_logged_in(page, timeout_ms: int) -> None:
    print("  Waiting for successful login…")
    page.wait_for_function(
        """() => {
            const href = window.location.href;
            return href.includes('account.riotgames.com')
                && !href.includes('auth.riotgames.com')
                && !href.includes('authenticate.riotgames.com')
                && !href.includes('/login');
        }""",
        timeout=timeout_ms,
    )


def mfa_fields_visible(page) -> bool:
    selectors = [
        'input[name*="code" i]',
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
        'input[placeholder*="code" i]',
        'input[aria-label*="code" i]',
    ]
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def fill_mfa_code(page, code: str) -> bool:
    return fill_first_matching(
        page,
        [
            'input[autocomplete="one-time-code"]',
            'input[name*="code" i]',
            'input[inputmode="numeric"]',
            'input[placeholder*="code" i]',
            'input[aria-label*="code" i]',
            'input[type="tel"]',
            'input[type="text"]',
        ],
        code,
        "MFA/email code",
    )


def try_solve_hcaptcha(page, api_key: str | None, proxy: str | None) -> bool:
    if not api_key:
        return False
    print("[captcha] Solving hCaptcha with CapSolver…")
    try:
        return solve_and_inject(page, api_key, proxy=proxy)
    except CapSolverError as exc:
        print(f"  CapSolver error: {exc}")
        return False
    except Exception as exc:
        print(f"  CapSolver unexpected error: {exc}")
        return False


def try_fill_imap_code(
    page,
    inbox: ImapInbox | None,
    *,
    since_epoch: float,
    timeout: float,
    used_codes: set[str],
) -> bool:
    if not inbox:
        return False
    if not mfa_fields_visible(page) and "code" not in page.content().lower():
        # Still try — Riot markup varies; wait_for_code is the expensive part only if we call it
        pass
    try:
        code = inbox.wait_for_code(
            since_epoch=since_epoch,
            timeout=timeout,
            used_codes=used_codes,
        )
    except Exception as exc:
        print(f"  IMAP code wait failed: {exc}")
        return False
    used_codes.add(code)
    if not fill_mfa_code(page, code):
        print(f"  Got code {code} but could not find an input — paste it manually.")
        pause(f"Enter code {code} in the browser, then continue.")
        return True
    click_first_matching(
        page,
        [
            'button[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Continue")',
            'button:has-text("Verify")',
            'button:has-text("Sign in")',
            'button:has-text("Confirm")',
        ],
        "code submit",
    )
    return True


def run_login(
    page,
    username: str,
    password: str,
    timeout_ms: int,
    *,
    capsolver_key: str | None,
    proxy: str | None,
    current_inbox: ImapInbox | None,
    imap_timeout: float,
) -> None:
    print("\n[1/3] Opening Riot account portal…")
    login_started = time.time()
    used_codes: set[str] = set()
    page.goto(ACCOUNT_URL, wait_until="domcontentloaded")

    try:
        page.wait_for_url(
            lambda url: "auth.riotgames.com" in url or "authenticate.riotgames.com" in url,
            timeout=15_000,
        )
    except Exception:
        if page_looks_logged_in(page):
            print("  Appears already signed in.")
            return

    print("[1/3] Filling login form…")
    filled_user = fill_first_matching(
        page,
        [
            'input[name="username"]',
            'input[type="text"][autocomplete="username"]',
            'input[type="email"]',
            "#username",
            'input[placeholder*="username" i]',
            'input[placeholder*="email" i]',
        ],
        username,
        "username",
    )
    filled_pass = fill_first_matching(
        page,
        [
            'input[name="password"]',
            'input[type="password"]',
            "#password",
            'input[autocomplete="current-password"]',
        ],
        password,
        "password",
    )

    page.wait_for_timeout(1_500)
    solved = try_solve_hcaptcha(page, capsolver_key, proxy)

    if not (filled_user and filled_pass):
        pause(
            "Could not auto-fill the login form. Sign in manually in the browser window."
        )
    else:
        clicked = click_first_matching(
            page,
            [
                'button[type="submit"]',
                'button:has-text("Sign in")',
                'button:has-text("Log in")',
                'button:has-text("Sign In")',
                '[data-testid="btn-signin-submit"]',
            ],
            "Sign in",
        )
        if not clicked:
            pause("Click Sign in yourself.")

        page.wait_for_timeout(2_000)
        if not page_looks_logged_in(page):
            if not solved or page.locator('iframe[src*="hcaptcha"]').count() > 0:
                solved = try_solve_hcaptcha(page, capsolver_key, proxy)
                if solved:
                    click_first_matching(
                        page,
                        [
                            'button[type="submit"]',
                            'button:has-text("Sign in")',
                            'button:has-text("Log in")',
                            'button:has-text("Sign In")',
                            '[data-testid="btn-signin-submit"]',
                        ],
                        "Sign in (after captcha)",
                    )

    # MFA / email code via IMAP
    page.wait_for_timeout(2_000)
    if not page_looks_logged_in(page):
        try_fill_imap_code(
            page,
            current_inbox,
            since_epoch=login_started - 5,
            timeout=imap_timeout,
            used_codes=used_codes,
        )

    try:
        wait_until_logged_in(page, min(timeout_ms, 60_000))
        print("  Login detected.")
        return
    except Exception:
        pass

    if capsolver_key and not page_looks_logged_in(page):
        print("  Login not finished yet — retrying CapSolver once…")
        if try_solve_hcaptcha(page, capsolver_key, proxy):
            click_first_matching(
                page,
                [
                    'button[type="submit"]',
                    'button:has-text("Sign in")',
                    'button:has-text("Log in")',
                    'button:has-text("Sign In")',
                ],
                "Sign in (retry)",
            )
            page.wait_for_timeout(2_000)
            try_fill_imap_code(
                page,
                current_inbox,
                since_epoch=login_started - 5,
                timeout=imap_timeout,
                used_codes=used_codes,
            )

    try:
        wait_until_logged_in(page, timeout_ms)
        print("  Login detected.")
    except Exception:
        pause(
            "Still waiting on login. Finish MFA/captcha in the browser if needed, "
            "then press Enter once you see your account page."
        )
        wait_until_logged_in(page, timeout_ms)
        print("  Login detected.")


def run_email_update(
    page,
    new_email: str,
    timeout_ms: int,
    *,
    current_inbox: ImapInbox | None,
    new_inbox: ImapInbox | None,
    imap_timeout: float,
) -> None:
    print("\n[2/3] Opening account settings…")
    page.goto(ACCOUNT_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2_000)

    navigated = False
    for path in ("/account", "/", "/#/"):
        try:
            page.goto(f"https://account.riotgames.com{path}", wait_until="domcontentloaded")
            page.wait_for_timeout(1_500)
            if AUTH_HOST_HINT in page.url or "authenticate.riotgames.com" in page.url:
                raise RuntimeError("Session expired — redirected to login")
            navigated = True
            break
        except Exception as exc:
            print(f"  Navigation note: {exc}")

    if not navigated:
        pause("Open your Riot account page manually in the browser.")

    print("[2/3] Looking for email / Personal Information controls…")
    opened_editor = click_first_matching(
        page,
        [
            'button:has-text("Edit")',
            'button:has-text("Change")',
            'a:has-text("Edit")',
            'button:has-text("Update")',
            '[aria-label*="email" i]',
            'button:has-text("Email")',
            'text=/change.*(email|e-mail)/i',
            'text=/update.*(email|e-mail)/i',
            'text=/edit.*(email|e-mail)/i',
        ],
        "email edit",
    )

    if not opened_editor:
        pause(
            "Locate Personal Information → Email on the page and open the edit/change flow. "
            "Leave the editor ready, then press Enter."
        )

    print("[3/3] Entering new email…")
    change_started = time.time()
    used_codes: set[str] = set()
    filled = fill_first_matching(
        page,
        [
            'input[type="email"]',
            'input[name*="email" i]',
            'input[id*="email" i]',
            'input[autocomplete="email"]',
            'input[placeholder*="email" i]',
        ],
        new_email,
        "new email",
    )
    if not filled:
        pause(f"Type the new email manually: {new_email}")

    # Confirm field if present
    fill_first_matching(
        page,
        [
            'input[name*="confirm" i]',
            'input[placeholder*="confirm" i]',
            'input[aria-label*="confirm" i]',
        ],
        new_email,
        "confirm email",
    )

    saved = click_first_matching(
        page,
        [
            'button:has-text("Save")',
            'button:has-text("Continue")',
            'button:has-text("Submit")',
            'button:has-text("Send")',
            'button:has-text("Verify")',
            'button[type="submit"]',
        ],
        "save / continue",
    )
    if not saved:
        pause("Click Save / Continue / Send verification yourself.")

    page.wait_for_timeout(2_000)

    # Current-inbox ownership code
    if current_inbox and mfa_fields_visible(page):
        print("  Waiting for verification code on CURRENT email via IMAP…")
        try_fill_imap_code(
            page,
            current_inbox,
            since_epoch=change_started - 5,
            timeout=imap_timeout,
            used_codes=used_codes,
        )
        page.wait_for_timeout(2_000)

    # New-inbox confirm code or verify link
    if new_inbox:
        print("  Waiting for verification on NEW email via IMAP…")
        link = None
        try:
            link = new_inbox.wait_for_verify_link(
                since_epoch=change_started - 5,
                timeout=min(60.0, imap_timeout),
            )
        except Exception:
            link = None
        if link:
            print(f"  Opening verify link…")
            page.goto(link, wait_until="domcontentloaded")
        else:
            try_fill_imap_code(
                page,
                new_inbox,
                since_epoch=change_started - 5,
                timeout=imap_timeout,
                used_codes=used_codes,
            )

    if current_inbox or new_inbox:
        print("\nDone (IMAP-assisted). Confirm the new email on account.riotgames.com.")
    else:
        pause(
            "Complete verification: enter codes from your CURRENT and/or NEW inbox. "
            "When Riot confirms the email change, press Enter here."
        )
        print("\nDone. Confirm the new email on account.riotgames.com if you have not already.")


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    args = parse_args()

    headed = args.headed if args.headed is not None else env_bool("HEADED", True)
    capsolver_key = None if args.no_captcha_solver else (
        args.capsolver_key or os.getenv("CAPSOLVER_API_KEY") or None
    )
    if capsolver_key:
        capsolver_key = capsolver_key.strip() or None
    proxy = (os.getenv("CAPSOLVER_PROXY") or os.getenv("PROXY") or "").strip() or None

    current_cfg = ImapConfig.from_env(env_map(), "IMAP")
    new_cfg = ImapConfig.from_env(env_map(), "NEW_IMAP")
    current_inbox = ImapInbox(current_cfg) if current_cfg else None
    new_inbox = ImapInbox(new_cfg) if new_cfg else None

    username = args.username or os.getenv("RIOT_USERNAME") or prompt("Riot username or email")
    password = os.getenv("RIOT_PASSWORD") or prompt("Riot password", secret=True)
    new_email = args.new_email or os.getenv("NEW_EMAIL") or prompt("New email address")

    print(
        "\nThis helper drives the official Riot account site for YOUR account only.\n"
        f"Browser mode: {'headed' if headed else 'headless'}\n"
        f"Target email: {new_email}\n"
        f"hCaptcha solver: {'CapSolver' if capsolver_key else 'manual'}\n"
        f"IMAP current inbox: {current_cfg.user if current_cfg else 'not set'}\n"
        f"IMAP new inbox: {new_cfg.user if new_cfg else 'not set'}\n"
    )

    if current_inbox:
        try:
            current_inbox.test_connection()
            print("  IMAP current inbox: connection OK")
        except Exception as exc:
            print(f"  IMAP current inbox: connection FAILED — {exc}")
            current_inbox = None
    if new_inbox:
        try:
            new_inbox.test_connection()
            print("  IMAP new inbox: connection OK")
        except Exception as exc:
            print(f"  IMAP new inbox: connection FAILED — {exc}")
            new_inbox = None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed. Run:\n"
            "  pip install -r requirements.txt\n"
            "  playwright install chromium",
            file=sys.stderr,
        )
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)

        try:
            run_login(
                page,
                username,
                password,
                args.timeout_ms,
                capsolver_key=capsolver_key,
                proxy=proxy,
                current_inbox=current_inbox,
                imap_timeout=args.imap_timeout,
            )
            run_email_update(
                page,
                new_email,
                args.timeout_ms,
                current_inbox=current_inbox,
                new_inbox=new_inbox,
                imap_timeout=args.imap_timeout,
            )
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130
        except Exception as exc:
            print(f"\nError: {exc}", file=sys.stderr)
            pause("Inspect the browser window if it is still open, then press Enter to exit.")
            return 1
        finally:
            context.close()
            browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
