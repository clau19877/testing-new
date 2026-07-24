#!/usr/bin/env python3
"""
Interactive helper: sign in to your Riot Games account and update its email.

Default captcha path is in-browser hybrid vision (local CV + 2Captcha
CoordinatesTask). Out-of-band token APIs soft-fail on Riot enterprise.

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
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

from dotenv import load_dotenv

from captcha import (
    CaptchaSolverError,
    install_rqdata_network_capture,
    solve_and_inject,
)
from imap_mail import ImapConfig, ImapInbox

ACCOUNT_URL = "https://account.riotgames.com/"
# Post-login account portal (where the email field lives).
# Override with EMAIL_CHANGE_URL / --email-change-url / CSV email_change_url.
AUTH_HOST_HINT = "auth.riotgames.com"

# Official account-portal OAuth login that includes email.edit scope.
# Matches authenticate.riotgames.com ?client_id=accountodactyl-prod&…
# The `state` query param is regenerated every run (one-time CSRF token).
ACCOUNT_OAUTH_SCOPES = " ".join(
    [
        "openid",
        "email",
        "profile",
        "riot://riot.atlas/openid",
        "riot://riot.atlas/accounts.edit",
        "riot://riot.atlas/accounts/password.edit",
        "riot://riot.atlas/accounts/email.edit",
        "riot://riot.atlas/accounts.auth",
        "riot://third_party.revoke",
        "riot://third_party.query",
        "riot://forgetme/notify.write",
        "riot://riot.authenticator/auth.code",
        "riot://riot.authenticator/authz.edit",
        "riot://rso/mfa/device.write",
        "riot://riot.authenticator/identity.add",
        "riot://riot.atlas/accounts.read",
        "riot://riot.parent-portal/parent.write",
    ]
)


def build_account_login_url(*, state: str | None = None) -> str:
    """
    Login URL for account.riotgames.com with email.edit (and related) scopes.

    Equivalent to the authenticate.riotgames.com link used to open the account
    portal for changing email — with a fresh `state` each call.
    """
    state = state or str(uuid.uuid4())
    authorize_qs = urlencode(
        {
            "acr_values": "urn:riot:gold",
            "client_id": "accountodactyl-prod",
            "redirect_uri": "https://account.riotgames.com/oauth2/log-in",
            "response_type": "code",
            "scope": ACCOUNT_OAUTH_SCOPES,
            "state": state,
        },
        quote_via=quote,
        safe="",
    )
    authorize_url = f"https://auth.riotgames.com/authorize?{authorize_qs}"
    authn_qs = urlencode(
        {
            "client_id": "accountodactyl-prod",
            "method": "riot_identity",
            "platform": "web",
            "redirect_uri": authorize_url,
            "security_profile": "high",
        },
        quote_via=quote,
        safe="",
    )
    return f"https://authenticate.riotgames.com/?{authn_qs}"


def resolve_login_url() -> str:
    """LOGIN_URL / ACCOUNT_LOGIN_URL env, else the email.edit OAuth login URL."""
    for key in ("LOGIN_URL", "ACCOUNT_LOGIN_URL"):
        raw = (os.getenv(key) or "").strip()
        if raw:
            # Pasted authenticate URL may carry a stale one-time state —
            # rebuild so we always get a fresh CSRF state.
            if "authenticate.riotgames.com" in raw and "accountodactyl-prod" in raw:
                return build_account_login_url()
            return raw
    return build_account_login_url()


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
    if os.getenv("NONINTERACTIVE", "").strip().lower() in {"1", "true", "yes"}:
        print(f"\n>>> {message}\n    (NONINTERACTIVE: continuing)")
        return
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
        "--captcha-provider",
        choices=(
            "manual", "vision", "hybrid", "capless", "capsolver",
            "capmonster", "twocaptcha", "nonecap",
        ),
        default=None,
        help="Captcha provider (default: CAPTCHA_PROVIDER or vision). "
        "'manual' pauses for a human to solve while the rest stays automated.",
    )
    parser.add_argument(
        "--captcha-key",
        help="Captcha API key (or set CAPLESS/CAPMONSTER/TWOCAPTCHA/NONECAP/CAPSOLVER_API_KEY)",
    )
    parser.add_argument(
        "--no-captcha-solver",
        action="store_true",
        help="Disable captcha solver even if an API key is configured",
    )
    parser.add_argument(
        "--imap-timeout",
        type=float,
        default=180.0,
        help="Seconds to wait for Riot mail via IMAP (default: 180)",
    )
    parser.add_argument(
        "--email-change-url",
        default=None,
        help="URL opened after login to edit email "
        "(default: EMAIL_CHANGE_URL env or https://account.riotgames.com/)",
    )
    parser.add_argument(
        "--login-url",
        default=None,
        help="Riot authenticate login URL (default: LOGIN_URL env or "
        "accountodactyl-prod OAuth with email.edit scopes)",
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


def dismiss_cookie_banner(page) -> None:
    click_first_matching(
        page,
        [
            'button.osano-cm-dialog__close',
            'button[aria-label="Close this dialog"]',
            'button:has-text("Accept")',
            'button:has-text("Agree")',
        ],
        "cookie dismiss",
    )


def click_riot_signin(page) -> bool:
    """Riot uses a red circular arrow button, not a labeled Sign in submit."""
    dismiss_cookie_banner(page)
    selectors = [
        'button[data-testid="btn-signin-submit"]',
        'button[type="submit"]',
        'button.mobile-button',
        'button:has(svg)',
        '[role="button"][data-testid*="submit" i]',
        'button:has-text("Sign in")',
        'button:has-text("Log in")',
        'button:has-text("Sign In")',
    ]
    if click_first_matching(page, selectors, "Sign in"):
        return True
    # Last resort: click the largest visible circular button near the form
    try:
        clicked = page.evaluate(
            """() => {
              const buttons = [...document.querySelectorAll('button')];
              const candidate = buttons.find(b => {
                const r = b.getBoundingClientRect();
                const style = getComputedStyle(b);
                return r.width > 40 && r.width < 120 && r.height > 40 && r.height < 120
                  && r.top > 200 && style.visibility !== 'hidden';
              });
              if (!candidate) return false;
              candidate.click();
              return true;
            }"""
        )
        if clicked:
            print("  Clicked Sign in via circular-button heuristic")
            return True
    except Exception:
        pass
    return False


def wait_for_manual_captcha(page, *, timeout_s: float = 300.0) -> bool:
    """
    Human-in-the-loop: pause while a person solves the hCaptcha in the browser
    window, then resume automatically. Success is detected when the challenge
    clears / the page progresses to MFA or the account. Everything before
    (credential fill, sign-in) and after (MFA via IMAP, email update) stays
    automated.
    """
    from vision_captcha import (
        captcha_visible,
        page_has_riot_oops,
        page_looks_auth_progress,
        page_looks_mfa,
    )

    # Wait for the challenge to mount after sign-in
    for _ in range(24):
        if (
            captcha_visible(page)
            or page_has_riot_oops(page)
            or page_looks_auth_progress(page)
        ):
            break
        page.wait_for_timeout(500)

    if page_has_riot_oops(page):
        print("  Riot Oops before manual solve — will rotate")
        return False
    if not captcha_visible(page):
        print("  No hCaptcha challenge visible — nothing to solve manually")
        return True

    print("\n" + "=" * 68, flush=True)
    print("  MANUAL CAPTCHA — solve the hCaptcha in the browser window now.", flush=True)
    print("  Automation will continue on its own the moment it clears.", flush=True)
    print(f"  Waiting up to {int(timeout_s)}s…", flush=True)
    print("=" * 68 + "\n", flush=True)

    deadline = time.time() + timeout_s
    last_log = 0.0
    while time.time() < deadline:
        page.wait_for_timeout(1000)
        if page_has_riot_oops(page):
            print("  Riot Oops during manual solve — will rotate")
            return False
        if not captcha_visible(page):
            page.wait_for_timeout(1500)
            if page_has_riot_oops(page):
                return False
            print("  Captcha cleared — resuming automation.")
            return True
        if page_looks_auth_progress(page) or page_looks_mfa(page):
            print("  Auth progressing — resuming automation.")
            return True
        remaining = deadline - time.time()
        if time.time() - last_log > 20:
            print(f"  …still waiting for manual solve ({int(remaining)}s left)", flush=True)
            last_log = time.time()
    print("  Manual captcha wait timed out.")
    return False


def try_solve_hcaptcha(
    page,
    *,
    provider: str,
    api_key: str | None,
    proxy: str | None,
) -> bool:
    provider = (provider or "").strip().lower()
    if provider in ("manual", "human"):
        timeout_s = float(os.getenv("MANUAL_CAPTCHA_TIMEOUT") or "300")
        return wait_for_manual_captcha(page, timeout_s=timeout_s)
    if provider in ("vision", "hybrid", "2cap_click"):
        from vision_captcha import (
            captcha_visible,
            page_has_riot_oops,
            solve_visible_captcha,
        )

        backend = (
            os.getenv("VISION_BACKEND")
            or ("hybrid" if provider in ("vision", "hybrid") else "twocaptcha")
        )
        print(f"[captcha] In-browser hybrid vision (backend={backend})…")
        # Wait for challenge mount after sign-in
        for _ in range(16):
            if captcha_visible(page) or page_has_riot_oops(page):
                break
            page.wait_for_timeout(500)
        if page_has_riot_oops(page):
            print("  Riot Oops page before vision — fail")
            return False
        if not captcha_visible(page):
            print("  No hCaptcha challenge visible")
            return False
        try:
            ok = solve_visible_captcha(page, backend=backend, max_rounds=5)
            print(f"  Vision captcha result={ok}")
            return bool(ok)
        except Exception as exc:
            print(f"  Vision captcha error: {exc}")
            return False

    if not api_key:
        return False
    print(f"[captcha] Solving hCaptcha with {provider}…")
    try:
        return solve_and_inject(page, provider=provider, api_key=api_key, proxy=proxy)
    except CaptchaSolverError as exc:
        print(f"  Captcha error: {exc}")
        return False
    except Exception as exc:
        print(f"  Captcha unexpected error: {exc}")
        return False


def wait_for_mfa_or_login(page, *, timeout_s: float = 20.0) -> str:
    """
    After captcha clears, poll briefly for MFA UI, account page, or Oops.
    Returns: 'logged_in' | 'mfa' | 'oops' | 'login' | 'unknown'
    """
    from vision_captcha import page_has_riot_oops, page_looks_mfa

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page_has_riot_oops(page):
            return "oops"
        if page_looks_logged_in(page):
            return "logged_in"
        if page_looks_mfa(page) or mfa_fields_visible(page):
            return "mfa"
        page.wait_for_timeout(500)
    if page_has_riot_oops(page):
        return "oops"
    if page_looks_logged_in(page):
        return "logged_in"
    if page_looks_mfa(page) or mfa_fields_visible(page):
        return "mfa"
    return "login"


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
    captcha_provider: str,
    captcha_key: str | None,
    captcha_proxy: str | None,
    current_inbox: ImapInbox | None,
    imap_timeout: float,
) -> str:
    """
    Drive Riot login. Returns one of:
      logged_in | mfa_done | oops | captcha_fail | timeout
    """
    from vision_captcha import captcha_visible, page_has_riot_oops

    print("\n[1/3] Opening Riot account portal (email.edit scopes)…")
    login_started = time.time()
    used_codes: set[str] = set()
    login_url = resolve_login_url()
    print(f"  Login URL: {login_url[:96]}…")
    page.goto(login_url, wait_until="domcontentloaded")

    try:
        page.wait_for_url(
            lambda url: "auth.riotgames.com" in url or "authenticate.riotgames.com" in url,
            timeout=15_000,
        )
    except Exception:
        if page_looks_logged_in(page):
            print("  Appears already signed in.")
            return "logged_in"

    dismiss_cookie_banner(page)
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

    if not (filled_user and filled_pass):
        pause(
            "Could not auto-fill the login form. Sign in manually in the browser window."
        )
    else:
        clicked = click_riot_signin(page)
        if not clicked:
            pause("Click the red arrow Sign in button yourself.")

    page.wait_for_timeout(2_000)
    if page_has_riot_oops(page):
        print("  Riot Oops right after sign-in")
        return "oops"

    use_vision = captcha_provider in ("vision", "hybrid", "2cap_click", "manual", "human")
    solved = False

    if use_vision:
        # Sign-in first, then solve in-browser (enterprise session bind)
        if captcha_visible(page) or not page_looks_logged_in(page):
            solved = try_solve_hcaptcha(
                page,
                provider=captcha_provider,
                api_key=captcha_key,
                proxy=captcha_proxy,
            )
            if page_has_riot_oops(page):
                return "oops"
            if not solved and captcha_visible(page):
                return "captcha_fail"
    else:
        # Legacy token inject (often soft-fails on Riot enterprise)
        page.wait_for_timeout(1_500)
        solved = try_solve_hcaptcha(
            page,
            provider=captcha_provider,
            api_key=captcha_key,
            proxy=captcha_proxy,
        )
        if filled_user and filled_pass:
            click_riot_signin(page)
            page.wait_for_timeout(2_000)
            if not page_looks_logged_in(page):
                if not solved or page.locator('iframe[src*="hcaptcha"]').count() > 0:
                    solved = try_solve_hcaptcha(
                        page,
                        provider=captcha_provider,
                        api_key=captcha_key,
                        proxy=captcha_proxy,
                    )
                    if solved:
                        click_riot_signin(page)

    # After captcha: wait for MFA / account / Oops before IMAP
    state = wait_for_mfa_or_login(page, timeout_s=20.0)
    print(f"  Post-captcha state={state}")
    if state == "oops":
        return "oops"
    if state == "logged_in":
        print("  Login detected.")
        return "logged_in"

    if state == "mfa" or not page_looks_logged_in(page):
        try_fill_imap_code(
            page,
            current_inbox,
            since_epoch=login_started - 5,
            timeout=imap_timeout,
            used_codes=used_codes,
        )
        page.wait_for_timeout(2_000)
        if page_looks_logged_in(page):
            print("  Login detected after MFA.")
            return "mfa_done"

    try:
        wait_until_logged_in(page, min(timeout_ms, 60_000))
        print("  Login detected.")
        return "logged_in"
    except Exception:
        pass

    # One more captcha attempt on same session (token providers only)
    if (
        not use_vision
        and captcha_key
        and not page_looks_logged_in(page)
        and not page_has_riot_oops(page)
    ):
        print("  Login not finished yet — retrying captcha once…")
        if try_solve_hcaptcha(
            page,
            provider=captcha_provider,
            api_key=captcha_key,
            proxy=captcha_proxy,
        ):
            click_riot_signin(page)
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
        return "logged_in"
    except Exception:
        if page_has_riot_oops(page):
            return "oops"
        pause(
            "Still waiting on login. Finish MFA/captcha in the browser if needed, "
            "then press Enter once you see your account page."
        )
        try:
            wait_until_logged_in(page, timeout_ms)
            return "logged_in"
        except Exception:
            return "timeout"
        print("  Login detected.")


def email_change_candidates(preferred: str | None) -> list[str]:
    """Ordered list of URLs to try for the email-change page (post-login)."""
    urls: list[str] = []
    preferred = (preferred or "").strip()
    if preferred:
        # Pasted authenticate URL → use fresh-state login builder instead;
        # after auth, account portal is where the email editor lives.
        if "authenticate.riotgames.com" in preferred and "accountodactyl-prod" in preferred:
            urls.append(ACCOUNT_URL)
        else:
            urls.append(preferred)
    env_url = (os.getenv("EMAIL_CHANGE_URL") or "").strip()
    for u in (
        env_url,
        ACCOUNT_URL,
        "https://account.riotgames.com/",
        "https://account.riotgames.com/#/",
        "https://account.riotgames.com/account",
    ):
        u = (u or "").strip()
        if not u:
            continue
        if "authenticate.riotgames.com" in u and "accountodactyl-prod" in u:
            u = ACCOUNT_URL
        if u not in urls:
            urls.append(u)
    return urls


def run_email_update(
    page,
    new_email: str,
    timeout_ms: int,
    *,
    current_inbox: ImapInbox | None,
    new_inbox: ImapInbox | None,
    imap_timeout: float,
    email_change_url: str | None = None,
) -> None:
    print("\n[2/3] Opening email-change page…")
    navigated = False
    for url in email_change_candidates(email_change_url):
        try:
            print(f"  Trying {url}")
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1_500)
            if AUTH_HOST_HINT in page.url or "authenticate.riotgames.com" in page.url:
                raise RuntimeError("Session expired — redirected to login")
            navigated = True
            print(f"  Opened: {page.url}")
            break
        except Exception as exc:
            print(f"  Navigation note: {exc}")

    if not navigated:
        pause(
            "Open the Riot email-change page manually in the browser "
            "(set EMAIL_CHANGE_URL if you have a direct link)."
        )

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
    # Default: in-browser hybrid (local CV + 2Captcha Coordinates). Token APIs
    # soft-fail on Riot enterprise even when they mint P1_ tokens.
    captcha_provider = (
        args.captcha_provider
        or os.getenv("CAPTCHA_PROVIDER")
        or "vision"
    ).strip().lower()
    if captcha_provider == "hybrid":
        captcha_provider = "vision"
    captcha_key = None
    if captcha_provider in ("manual", "human"):
        # Human solves the captcha; no solver key needed. Force headed and a
        # single session so we don't rotate the proxy mid-solve.
        headed = True
        os.environ.setdefault("PROXY_ATTEMPTS", "1")
        print(
            "[mode] MANUAL captcha — a human solves hCaptcha in the browser; "
            "login form, MFA (IMAP) and email update stay automated."
        )
    if not args.no_captcha_solver and captcha_provider not in ("manual", "human"):
        key_env = {
            "capless": "CAPLESS_API_KEY",
            "capsolver": "CAPSOLVER_API_KEY",
            "capmonster": "CAPMONSTER_API_KEY",
            "twocaptcha": "TWOCAPTCHA_API_KEY",
            "nonecap": "NONECAP_API_KEY",
            "vision": "TWOCAPTCHA_API_KEY",
        }.get(captcha_provider)
        captcha_key = (
            args.captcha_key
            or (os.getenv(key_env) if key_env else None)
            or os.getenv("TWOCAPTCHA_API_KEY")
            or os.getenv("TWO_CAPTCHA_API_KEY")
            or os.getenv("CAPLESS_API_KEY")
            or os.getenv("CAPTCHA_API_KEY")
            or os.getenv("CAPMONSTER_API_KEY")
            or os.getenv("NONECAP_API_KEY")
            or os.getenv("CAPSOLVER_API_KEY")
            or None
        )
    if captcha_key:
        captcha_key = captcha_key.strip() or None

    from proxyutil import pick_proxy, proxy_count, to_playwright

    proxy_list_path = os.getenv("PROXY_LIST") or "data/proxies.txt"
    proxy_index = int(os.getenv("PROXY_INDEX") or "0")
    max_proxy_attempts = int(os.getenv("PROXY_ATTEMPTS") or "3")
    n_proxies = proxy_count(proxy_list_path)

    captcha_proxy = (
        os.getenv("CAPLESS_PROXY")
        or os.getenv("CAPTCHA_PROXY")
        or os.getenv("BROWSER_PROXY")
        or os.getenv("CAPSOLVER_PROXY")
        or os.getenv("PROXY")
        or ""
    ).strip() or None
    if not captcha_proxy:
        captcha_proxy = pick_proxy(None, proxy_list_path, index=proxy_index)

    browser_proxy = (
        os.getenv("BROWSER_PROXY") or captcha_proxy or ""
    ).strip() or None

    # Vision path needs TWOCAPTCHA_API_KEY for Coordinates fallback (or local-only)
    if captcha_provider == "vision":
        os.environ.setdefault("VISION_BACKEND", "hybrid")
        if not (os.getenv("TWOCAPTCHA_API_KEY") or os.getenv("TWO_CAPTCHA_API_KEY")):
            print(
                "NOTE: VISION_BACKEND=hybrid works best with TWOCAPTCHA_API_KEY "
                "for Coordinates fallback when local CV misses.\n"
            )

    current_cfg = ImapConfig.from_env(env_map(), "IMAP")
    new_cfg = ImapConfig.from_env(env_map(), "NEW_IMAP")
    current_inbox = ImapInbox(current_cfg) if current_cfg else None
    new_inbox = ImapInbox(new_cfg) if new_cfg else None

    username = args.username or os.getenv("RIOT_USERNAME") or prompt("Riot username or email")
    password = os.getenv("RIOT_PASSWORD") or prompt("Riot password", secret=True)
    new_email = args.new_email or os.getenv("NEW_EMAIL") or prompt("New email address")
    email_change_url = (
        (args.email_change_url or os.getenv("EMAIL_CHANGE_URL") or ACCOUNT_URL)
        .strip()
        or ACCOUNT_URL
    )
    if args.login_url:
        os.environ["LOGIN_URL"] = args.login_url.strip()
    login_url_preview = resolve_login_url()

    print(
        "\nThis helper drives the official Riot account site for YOUR account only.\n"
        f"Browser mode: {'headed' if headed else 'headless'}\n"
        f"Target email: {new_email}\n"
        f"Login URL: {login_url_preview[:96]}…\n"
        f"Email-change URL: {email_change_url}\n"
        f"Captcha: {captcha_provider} "
        f"(VISION_BACKEND={os.getenv('VISION_BACKEND') or 'hybrid'})\n"
        f"Proxy list: {n_proxies} entries, start index={proxy_index}, "
        f"attempts={max_proxy_attempts}\n"
        f"IMAP current inbox: {current_cfg.user if current_cfg else 'not set'}\n"
        f"IMAP new inbox: {new_cfg.user if new_cfg else 'not set'}\n"
    )
    if captcha_provider == "capsolver":
        print(
            "NOTE: CapSolver currently rejects Riot's hCaptcha sitekey.\n"
            "      Prefer CAPTCHA_PROVIDER=vision + TWOCAPTCHA_API_KEY.\n"
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
        from stealth_browser import browser_engine, launch_stealth_browser
    except ImportError:
        print(
            "Stealth browser helper missing. Ensure tools/riot-email-update "
            "is intact and deps installed (patchright / playwright).",
            file=sys.stderr,
        )
        return 1

    login_ok = False
    last_status = "unknown"
    print(
        f"  Browser engine: {browser_engine()} "
        f"(HUMAN_MOUSE={os.getenv('HUMAN_MOUSE', '1')})",
        flush=True,
    )
    for attempt in range(max_proxy_attempts):
        idx = (proxy_index + attempt) % max(n_proxies, 1) if n_proxies else proxy_index + attempt
        session_proxy = (
            pick_proxy(None, proxy_list_path, index=idx)
            if n_proxies
            else browser_proxy
        )
        # Prefer rotating list over a single BROWSER_PROXY when retrying
        if attempt > 0 and n_proxies:
            session_proxy = pick_proxy(None, proxy_list_path, index=idx)
        captcha_proxy_attempt = session_proxy or captcha_proxy

        if session_proxy:
            print(
                f"\n=== Session attempt {attempt + 1}/{max_proxy_attempts} "
                f"proxy_index={idx} "
                f"server={to_playwright(session_proxy)['server']} ==="
            )
        else:
            print(
                f"\n=== Session attempt {attempt + 1}/{max_proxy_attempts} "
                f"(no proxy) ==="
            )

        try:
            with launch_stealth_browser(
                headed=headed, proxy=session_proxy
            ) as (_p, _browser, context):
                page = context.new_page()
                page.set_default_timeout(args.timeout_ms)
                # Capture fresh enterprise rqdata from Riot API responses for token path
                install_rqdata_network_capture(page)

                last_status = run_login(
                    page,
                    username,
                    password,
                    args.timeout_ms,
                    captcha_provider=captcha_provider,
                    captcha_key=captcha_key,
                    captcha_proxy=captcha_proxy_attempt,
                    current_inbox=current_inbox,
                    imap_timeout=args.imap_timeout,
                )
                print(f"  Login status: {last_status}")
                if last_status in ("logged_in", "mfa_done"):
                    login_ok = True
                    run_email_update(
                        page,
                        new_email,
                        args.timeout_ms,
                        current_inbox=current_inbox,
                        new_inbox=new_inbox,
                        imap_timeout=args.imap_timeout,
                        email_change_url=email_change_url,
                    )
                elif last_status in ("oops", "captcha_fail"):
                    print(
                        "  Captcha/session soft-fail — rotating proxy + fresh browser context"
                    )
                else:
                    print(f"  Login incomplete ({last_status}) — rotating proxy")
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130
        except Exception as exc:
            print(f"\nError: {exc}", file=sys.stderr)
            pause("Inspect the browser window if it is still open, then press Enter to exit.")
            last_status = "error"

        if login_ok:
            break
        # fall through to next proxy attempt

    if not login_ok:
        print(
            f"\nLogin did not succeed after {max_proxy_attempts} proxy attempt(s) "
            f"(last={last_status}).",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
