"""
Stealth browser launch helpers (Patchright / Camoufox / Playwright).

BROWSER_ENGINE:
  patchright  — patched Chromium (default; Multibot-style)
  camoufox    — stealth Firefox with built-in humanize
  playwright  — stock Playwright Chromium
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


def browser_engine() -> str:
    return (os.getenv("BROWSER_ENGINE") or "patchright").strip().lower()


def launch_args() -> list[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]


@contextmanager
def launch_stealth_browser(
    *,
    headed: bool = True,
    proxy: str | None = None,
    user_agent: str | None = None,
    viewport: dict[str, int] | None = None,
) -> Iterator[tuple[Any, Any, Any]]:
    """
    Yield (playwright_or_none, browser, context).

    For Camoufox, playwright_or_none is the Camoufox context manager object
    and browser may be the same as context's browser.
    Caller must close context/browser; this helper closes on exit.
    """
    from proxyutil import to_playwright

    engine = browser_engine()
    vp = viewport or {"width": 1280, "height": 900}
    ua = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    proxy_dict = to_playwright(proxy) if proxy else None

    if engine in ("camoufox", "fox"):
        try:
            from camoufox.sync_api import Camoufox
        except ImportError as exc:
            raise RuntimeError(
                "Camoufox not installed. pip install 'camoufox[geoip]' "
                "&& camoufox fetch"
            ) from exc

        kw: dict[str, Any] = {
            "headless": not headed,
            "humanize": True,
            "os": ["windows"],
            "window": (int(vp["width"]), int(vp["height"])),
        }
        # Match fingerprint geo to residential proxy egress when possible
        if proxy_dict:
            kw["proxy"] = proxy_dict
            kw["geoip"] = True
        print(
            f"[stealth] engine=camoufox headed={headed} "
            f"proxy={'yes' if proxy else 'no'} humanize=True",
            flush=True,
        )
        # Camoufox manages its own fingerprint; avoid forcing Chrome UA on Firefox
        with Camoufox(**kw) as browser:
            # Prefer the default persistent context when available
            try:
                context = browser.new_context(locale="en-US")
            except TypeError:
                context = browser.new_context()
            try:
                yield None, browser, context
            finally:
                try:
                    context.close()
                except Exception:
                    pass
        return

    if engine in ("patchright", "patch", "multibot"):
        try:
            from patchright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Patchright not installed. pip install patchright "
                "&& python -m patchright install chromium"
            ) from exc
        api_name = "patchright"
    else:
        from playwright.sync_api import sync_playwright

        api_name = "playwright"

    print(f"[stealth] engine={api_name} headed={headed} proxy={'yes' if proxy else 'no'}", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=launch_args(),
            channel=None,
        )
        ctx_kwargs: dict[str, Any] = {
            "viewport": vp,
            "locale": "en-US",
            "user_agent": ua,
        }
        if proxy_dict:
            ctx_kwargs["proxy"] = proxy_dict
        # Patchright: reduce automation signals further when supported
        try:
            context = browser.new_context(**ctx_kwargs)
        except TypeError:
            context = browser.new_context(
                viewport=vp,
                locale="en-US",
                user_agent=ua,
                proxy=proxy_dict,
            )
        try:
            # Best-effort: hide webdriver
            context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = window.chrome || { runtime: {} };
                """
            )
        except Exception:
            pass
        try:
            yield p, browser, context
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
