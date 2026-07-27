"""Pre-warmed browser cart — survive HTML crashes during drops.

Problem:
  During drops the product HTML often 502/503 while the cart API can still work.
  Cold-starting Chrome or reloading the PDP at T-0 frequently fails.

Solution:
  1. Before the drop, park logged-in Chrome tabs on the product page (warm pool).
  2. Poll eligibility via lightweight JSON APIs (not HTML).
  3. At drop time, fire cart from *inside* the already-open page via fetch().
     F5/WAF page-context headers are attached by the site's own JS — no reload.
  4. Only fall back to clicking PLACE PRE-ORDER if in-page fetch fails.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .browser_cart import (
    _click_add_to_cart,
    _load_cookies_into_driver,
    _page_fetch_add_to_cart,
    _safe_cart_count,
    _wait_for_product_ready,
    verify_browser_logged_in,
)
from .logging_utils import get_logger, log_exception
from .proxy_util import redact_proxy
from .session_login import _create_webdriver, _driver_cookies
from .sessions import RuntimeSession, apply_cookies_to_client, load_cookies_file

if TYPE_CHECKING:
    from .api import PBandaiHkClient
    from .config import Config

logger = get_logger("warm_cart")


@dataclass
class WarmBrowser:
    name: str
    client: "PBandaiHkClient"
    driver: Any = None
    product_code: str = ""
    cookie_file: str = ""
    ready: bool = False
    last_error: str = ""


@dataclass
class WarmBrowserPool:
    """Keep one logged-in Chrome tab per session parked on the product page."""

    config: "Config"
    browsers: List[WarmBrowser] = field(default_factory=list)
    product_code: str = ""

    @property
    def ready_count(self) -> int:
        return sum(1 for b in self.browsers if b.ready and b.driver is not None)

    def prepare(
        self,
        sessions: List[RuntimeSession],
        product_code: str,
    ) -> int:
        """Open headed browsers, inject cookies, park on product page."""
        self.close()
        self.product_code = product_code
        product_url = self._product_url(product_code)
        # Prefer headed; headless often hits "Page not available".
        original_bg = bool(self.config.background_mode)
        self.config.background_mode = False
        ready = 0
        try:
            for runtime in sessions:
                wb = self._open_one(runtime, product_code, product_url)
                self.browsers.append(wb)
                if wb.ready:
                    ready += 1
        finally:
            self.config.background_mode = original_bg
        logger.info(
            "[warm] pool ready=%s/%s on %s",
            ready,
            len(sessions),
            product_code,
        )
        print(f"[warm] browsers ready={ready}/{len(sessions)} parked on {product_code}")
        return ready

    def ensure_product(self, product_code: str) -> None:
        """Re-park warm browsers if product changed (no full relaunch)."""
        if not product_code:
            return
        if product_code == self.product_code and all(
            b.ready and b.product_code == product_code for b in self.browsers if b.driver
        ):
            return
        product_url = self._product_url(product_code)
        home = f"{self.config.base_url}/{self.config.area_code}/"
        for wb in self.browsers:
            if wb.driver is None:
                continue
            try:
                url = (wb.driver.current_url or "").lower()
                title = (wb.driver.title or "").lower()
                if (
                    product_code.lower() in url
                    and "page not available" not in title
                ):
                    wb.product_code = product_code
                    wb.ready = True
                    continue
                logger.info("[warm] repark %s -> %s", wb.name, product_code)
                print(f"[warm] repark {wb.name} -> {product_code}")
                wb.driver.get(product_url)
                _wait_for_product_ready(wb.driver, timeout=45)
                title = (wb.driver.title or "").lower()
                if "page not available" in title:
                    logger.warning(
                        "[warm] repark PDP down for %s; staying on HK home",
                        wb.name,
                    )
                    wb.driver.get(home)
                    time.sleep(1.0)
                wb.product_code = product_code
                wb.ready = True
                wb.last_error = ""
            except Exception as exc:  # noqa: BLE001
                # Keep browser alive on HK origin if possible.
                try:
                    wb.driver.get(home)
                    time.sleep(1.0)
                    wb.product_code = product_code
                    wb.ready = True
                    wb.last_error = f"repark fallback home: {exc}"
                    logger.warning("[warm] %s repark fallback home: %s", wb.name, exc)
                except Exception as exc2:  # noqa: BLE001
                    wb.ready = False
                    wb.last_error = str(exc2)
                    log_exception(logger, f"warm repark failed session={wb.name}", exc2)
        self.product_code = product_code

    def add_to_cart(
        self,
        client: "PBandaiHkClient",
        *,
        product_code: str,
        area_item_no: str,
        qty: int = 1,
    ) -> tuple[bool, str]:
        wb = self._find(client.name)
        if wb is None or not wb.ready or wb.driver is None:
            return False, "warm browser not ready for session"

        if product_code and product_code != wb.product_code:
            self.ensure_product(product_code)
            if not wb.ready:
                return False, f"warm repark failed: {wb.last_error or 'unknown'}"

        before = _safe_cart_count(client)
        csrf = getattr(client, "csrf_token", "") or ""

        # 1) In-page fetch — no navigation / no HTML reload. F5 hooks attach tokens.
        ok, note = _page_fetch_add_to_cart(
            wb.driver,
            area_item_no=area_item_no,
            qty=qty,
            csrf=csrf,
        )
        if ok:
            return self._verify(client, wb, before, f"warm-inpage {note}")

        # 2) Click PLACE PRE-ORDER / ADD TO CART on the already-open page.
        logger.warning(
            "[warm] in-page fetch failed session=%s (%s); trying click",
            client.name,
            note,
        )
        print(f"[{client.name}] warm fetch failed ({note[:120]}); clicking button")
        clicked = _click_add_to_cart(wb.driver, timeout=12)
        if clicked:
            time.sleep(2.0)
            return self._verify(client, wb, before, "warm-click PLACE PRE-ORDER/CART")

        return False, f"warm failed: fetch={note}; click=no button"

    def close(self) -> None:
        for wb in self.browsers:
            if wb.driver is None:
                continue
            try:
                wb.driver.quit()
            except Exception as exc:  # noqa: BLE001
                log_exception(logger, f"warm driver.quit failed session={wb.name}", exc)
            wb.driver = None
            wb.ready = False
        self.browsers = []
        self.product_code = ""

    def _find(self, name: str) -> Optional[WarmBrowser]:
        for wb in self.browsers:
            if wb.name == name:
                return wb
        return None

    def _product_url(self, product_code: str) -> str:
        return f"{self.config.base_url}/{self.config.area_code}/item/{product_code}"

    def _cookies_for(self, runtime: RuntimeSession) -> List[Dict[str, Any]]:
        cookie_file = getattr(runtime.spec, "cookie_file", "") or ""
        cookies: List[Dict[str, Any]] = []
        if cookie_file and Path(str(cookie_file)).exists():
            cookies = load_cookies_file(Path(str(cookie_file)))
        if cookies:
            return cookies
        for c in runtime.client.session.cookies:
            rest = getattr(c, "_rest", {}) or {}
            cookies.append(
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain or ".p-bandai.com",
                    "path": c.path or "/",
                    "secure": bool(getattr(c, "secure", True)),
                    "httpOnly": bool(
                        rest.get("HttpOnly") is not None
                        or str(c.name or "").upper().startswith("SESSION")
                    ),
                }
            )
        return cookies

    def _open_one(
        self,
        runtime: RuntimeSession,
        product_code: str,
        product_url: str,
    ) -> WarmBrowser:
        client = runtime.client
        cookie_file = getattr(runtime.spec, "cookie_file", "") or ""
        wb = WarmBrowser(
            name=client.name,
            client=client,
            product_code=product_code,
            cookie_file=cookie_file,
        )
        cookies = self._cookies_for(runtime)
        if not cookies:
            wb.last_error = "no cookies — login first"
            logger.warning("[warm] skip %s: %s", client.name, wb.last_error)
            print(f"[warm] skip {client.name}: {wb.last_error}")
            return wb

        proxy = client.proxy or self.config.proxy_url or ""
        driver = None
        try:
            print(
                f"[warm] parking {client.name} on {product_code} "
                f"(proxy={redact_proxy(proxy) or '-'})"
            )
            driver = _create_webdriver(self.config, proxy=proxy)
            try:
                driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {
                        "source": (
                            "Object.defineProperty(navigator, 'webdriver', "
                            "{get: () => undefined})"
                        )
                    },
                )
            except Exception:  # noqa: BLE001
                pass

            home = f"{self.config.base_url}/{self.config.area_code}/"
            driver.get(home)
            time.sleep(1.2)
            cookie_stats = _load_cookies_into_driver(driver, cookies)
            logger.info(
                "[warm] cookies session=%s total=%s applied=%s sessionCookies=%s failed=%s",
                client.name,
                cookie_stats.get("total"),
                cookie_stats.get("applied"),
                cookie_stats.get("session"),
                cookie_stats.get("failed"),
            )
            print(
                f"[warm] {client.name}: cookies applied="
                f"{cookie_stats.get('applied')}/{cookie_stats.get('total')} "
                f"SESSION={cookie_stats.get('session')}"
            )
            # Hard reload so Vue boots with SESSION (otherwise header stays Sign In).
            driver.get(home)
            time.sleep(1.0)
            auth = verify_browser_logged_in(driver, timeout=15.0)
            if not auth.get("ok"):
                _load_cookies_into_driver(driver, cookies)
                driver.get(home)
                time.sleep(1.0)
                auth = verify_browser_logged_in(driver, timeout=12.0)
            if auth.get("ok"):
                who = auth.get("email") or auth.get("member_id") or "yes"
                print(f"[warm] {client.name}: logged in OK ({who})")
                logger.info("[warm] login OK session=%s auth=%s", client.name, auth)
            else:
                print(
                    f"[warm] {client.name}: NOT logged in in browser "
                    f"(SESSION={auth.get('has_session')} reason={str(auth.get('reason'))[:80]})"
                )
                logger.warning("[warm] login verify failed session=%s auth=%s", client.name, auth)
                wb.last_error = f"browser not logged in: {auth.get('reason')}"

            driver.get(product_url)
            _wait_for_product_ready(driver, timeout=60)
            title = (driver.title or "").lower()
            page_ok = "page not available" not in title
            if not page_ok:
                # Soft retry once — site flaky before drop
                time.sleep(2.0)
                driver.get(product_url)
                _wait_for_product_ready(driver, timeout=40)
                title = (driver.title or "").lower()
                page_ok = "page not available" not in title

            if not page_ok:
                # Critical for drops: HTML PDP may 502 while cart API works.
                # Stay on HK origin so F5 hooks + cookies are live; in-page fetch
                # still posts /api/cart/addToCart without needing the PDP DOM.
                logger.warning(
                    "[warm] %s PDP unavailable; parking on %s for in-page fetch",
                    client.name,
                    home,
                )
                print(
                    f"[warm] {client.name}: PDP PAGE NOT AVAILABLE — "
                    f"parking on HK home for in-page cart fetch"
                )
                driver.get(home)
                time.sleep(1.5)
                wb.driver = driver
                # Still mark ready for in-page fetch if SESSION exists; cart needs auth.
                wb.ready = bool(auth.get("ok") or auth.get("has_session") or cookie_stats.get("session"))
                wb.product_code = product_code
                if not wb.last_error:
                    wb.last_error = "pdp unavailable; home-parked"
                else:
                    wb.last_error = f"{wb.last_error}; pdp unavailable; home-parked"
                logger.info(
                    "[warm] home-parked session=%s ready=%s logged_in=%s",
                    client.name,
                    wb.ready,
                    auth.get("ok"),
                )
                return wb

            # Nudge page JS / F5 hooks
            time.sleep(1.0)
            try:
                driver.execute_script("window.scrollTo(0, 400);")
            except Exception:
                pass

            wb.driver = driver
            # Prefer logged-in browsers; still park if SESSION cookie landed (UI may lag).
            wb.ready = bool(auth.get("ok") or auth.get("has_session") or cookie_stats.get("session"))
            if not wb.ready:
                wb.last_error = wb.last_error or "no SESSION after cookie inject"
            logger.info(
                "[warm] ready=%s session=%s logged_in=%s url=%s",
                wb.ready,
                client.name,
                auth.get("ok"),
                driver.current_url,
            )
            print(
                f"[warm] {'ready' if wb.ready else 'NOT ready'} {client.name}"
                f"{'' if auth.get('ok') else ' (check Sign In / re-login)'}"
            )
        except Exception as exc:  # noqa: BLE001
            wb.ready = False
            wb.last_error = str(exc)
            log_exception(logger, f"warm open failed session={client.name}", exc)
            print(f"[warm] failed {client.name}: {exc}")
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            wb.driver = None
        return wb

    def _verify(
        self,
        client: "PBandaiHkClient",
        wb: WarmBrowser,
        before: Optional[int],
        note: str,
    ) -> tuple[bool, str]:
        try:
            fresh = _driver_cookies(wb.driver)
            if fresh:
                apply_cookies_to_client(client, fresh)
                if wb.cookie_file:
                    Path(str(wb.cookie_file)).write_text(
                        json.dumps(fresh, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
            client.refresh_csrf()
            after = _safe_cart_count(client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("warm cart verify failed session=%s: %s", client.name, exc)
            after = None

        if after is not None and before is not None and after > before:
            return True, f"{note} cart {before}->{after}"
        if after is not None and after > 0 and (before or 0) == 0:
            return True, f"{note} cart_count={after}"
        if after is not None and before is not None and after <= before:
            return False, f"{note} but cart unchanged {before}->{after}"
        return True, f"{note} (verify cart on site)"
