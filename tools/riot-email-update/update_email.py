#!/usr/bin/env python3
"""
Interactive helper: sign in to your Riot Games account and update its email.

Uses the official portal at https://account.riotgames.com/
hCaptcha can be solved automatically via CapSolver when CAPSOLVER_API_KEY is set.

Usage:
  cd tools/riot-email-update
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  playwright install chromium
  cp .env.example .env   # set CAPSOLVER_API_KEY (+ Riot creds)
  python update_email.py
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from captcha import CapSolverError, solve_and_inject

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log into your Riot account and update the email address."
    )
    parser.add_argument(
        "--username",
        help="Riot username or email (or set RIOT_USERNAME)",
    )
    parser.add_argument(
        "--new-email",
        help="New email address (or set NEW_EMAIL)",
    )
    parser.add_argument(
        "--headed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show the browser window (default: true / HEADED env)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=120_000,
        help="Default Playwright timeout in ms (default: 120000)",
    )
    parser.add_argument(
        "--capsolver-key",
        help="CapSolver API key (or set CAPSOLVER_API_KEY)",
    )
    parser.add_argument(
        "--no-captcha-solver",
        action="store_true",
        help="Disable CapSolver even if an API key is configured",
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


def wait_until_logged_in(page, timeout_ms: int) -> None:
    """Stay on auth until we land back on the account portal."""
    print("  Waiting for successful login (MFA may still be required)…")
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


def run_login(
    page,
    username: str,
    password: str,
    timeout_ms: int,
    *,
    capsolver_key: str | None,
    proxy: str | None,
) -> None:
    print("\n[1/3] Opening Riot account portal…")
    page.goto(ACCOUNT_URL, wait_until="domcontentloaded")

    # Redirect to auth is expected when logged out
    try:
        page.wait_for_url(
            lambda url: "auth.riotgames.com" in url or "authenticate.riotgames.com" in url,
            timeout=15_000,
        )
    except Exception:
        if "account.riotgames.com" in page.url and AUTH_HOST_HINT not in page.url:
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

    # Solve captcha before / around submit when CapSolver is configured
    page.wait_for_timeout(1_500)
    solved = try_solve_hcaptcha(page, capsolver_key, proxy)

    if not (filled_user and filled_pass):
        pause(
            "Could not auto-fill the login form. Sign in manually in the browser window, "
            "including any captcha / MFA."
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
            pause("Click Sign in yourself, then finish captcha / MFA if shown.")

        # Riot sometimes shows hCaptcha only after the first submit attempt
        page.wait_for_timeout(2_000)
        if "account.riotgames.com" not in page.url or "auth" in page.url:
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

    try:
        wait_until_logged_in(page, timeout_ms)
        print("  Login detected.")
    except Exception:
        if capsolver_key:
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
                try:
                    wait_until_logged_in(page, timeout_ms)
                    print("  Login detected.")
                    return
                except Exception:
                    pass
        pause(
            "Still waiting on login. Finish MFA (or captcha if CapSolver failed) in the browser, "
            "then press Enter once you see your account page."
        )
        wait_until_logged_in(page, timeout_ms)
        print("  Login detected.")


def run_email_update(page, new_email: str, timeout_ms: int) -> None:
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

    pause(
        "Complete verification: enter codes from your CURRENT and/or NEW inbox in the browser. "
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

    username = args.username or os.getenv("RIOT_USERNAME") or prompt("Riot username or email")
    password = os.getenv("RIOT_PASSWORD") or prompt("Riot password", secret=True)
    new_email = args.new_email or os.getenv("NEW_EMAIL") or prompt("New email address")

    print(
        "\nThis helper drives the official Riot account site for YOUR account only.\n"
        f"Browser mode: {'headed' if headed else 'headless'}\n"
        f"Target email: {new_email}\n"
        f"hCaptcha solver: {'CapSolver' if capsolver_key else 'manual (no CAPSOLVER_API_KEY)'}\n"
    )
    if not capsolver_key:
        print(
            "Tip: set CAPSOLVER_API_KEY in .env to auto-solve Riot hCaptcha.\n"
            "     Get a key at https://www.capsolver.com/\n"
        )

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
            )
            run_email_update(page, new_email, args.timeout_ms)
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
