"""Pre-warmed browser cart — survive HTML crashes during drops.

Problem:
  During drops the product HTML often 502/503 while the cart API can still work.
  Cold-starting Chrome or reloading the PDP at T-0 frequently fails.
  Raw in-page fetch('/api/cart/addToCart') often gets WAF 501 HTML because it
  lacks Shape/F5 bot tokens that the site's own PLACE PRE-ORDER click carries.

Solution:
  1. Before the drop, park logged-in Chrome tabs on the product page (warm pool).
  2. Poll eligibility via lightweight JSON APIs (not HTML).
  3. At drop time: click PLACE PRE-ORDER first when the button is on the PDP.
  4. Only use a short in-page fetch burst as fallback (home-park / no button).
  5. Never repark after a successful click — reloads destroy WAF context.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .browser_cart import (
    _click_add_to_cart,
    _is_retryable_cart_note,
    _is_stock_or_business_cart_error,
    _load_cookies_into_driver,
    _page_fetch_add_to_cart_burst,
    _safe_cart_count,
    _wait_for_product_ready,
    force_vue_member_refresh,
    session_cookie_value,
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
        on_pdp = self._is_on_product_page(wb, product_code)
        clicked = False
        note = ""

        # 1) Click-first on PDP — site button carries Shape/F5 tokens; raw fetch often 501s.
        if on_pdp:
            print(f"[{client.name}] warm click-first PLACE PRE-ORDER")
            logger.info("[warm] click-first session=%s on_pdp=1", client.name)
            clicked = _click_add_to_cart(wb.driver, timeout=3)
            if clicked:
                verified = self._verify(
                    client,
                    wb,
                    before,
                    "warm-click PLACE PRE-ORDER/CART",
                    poll_seconds=7.0,
                )
                if verified[0]:
                    return verified
                # Click fired but cart not reflected yet — one short fetch as backup,
                # then stop. Do NOT repark (destroys page WAF context).
                ok, note = _page_fetch_add_to_cart_burst(
                    wb.driver,
                    area_item_no=area_item_no,
                    qty=qty,
                    csrf=csrf,
                    attempts=3,
                    gap=0.15,
                )
                if ok:
                    return self._verify(
                        client, wb, before, f"warm-click+fetch {note}", poll_seconds=4.0
                    )
                if _is_stock_or_business_cart_error(note):
                    msg = (
                        "stock/preallocation hold "
                        f"(Bandai treats as OOS despite UI qty): {note}"
                    )
                    logger.warning("[warm] %s session=%s", msg[:180], client.name)
                    print(f"[{client.name}] {msg[:160]}")
                    return False, msg
                return False, (
                    f"warm failed: click=yes cart unchanged; fetch={note or 'n/a'}"
                )

        # 2) Short fetch burst (home-park / no button). Cap hard — 501 spam trips WAF.
        ok, note = _page_fetch_add_to_cart_burst(
            wb.driver,
            area_item_no=area_item_no,
            qty=qty,
            csrf=csrf,
            attempts=4,
            gap=0.15,
        )
        if ok:
            return self._verify(client, wb, before, f"warm-inpage {note}")

        stock_blocked = _is_stock_or_business_cart_error(note)
        if stock_blocked:
            # One native click then poll — sometimes cart reflects after 409.
            if on_pdp and not clicked:
                clicked = _click_add_to_cart(wb.driver, timeout=3)
                if clicked:
                    verified = self._verify(
                        client,
                        wb,
                        before,
                        "warm-click after preallocation",
                        poll_seconds=6.0,
                    )
                    if verified[0]:
                        return verified
            msg = (
                f"stock/preallocation hold (Bandai treats as OOS despite UI qty): {note}"
            )
            logger.warning("[warm] %s session=%s", msg[:180], client.name)
            print(f"[{client.name}] {msg[:160]}")
            return False, msg

        # 3) Fetch failed (likely 501) — try click if we haven't yet.
        if not clicked:
            logger.warning(
                "[warm] short fetch failed session=%s (%s); trying click",
                client.name,
                note[:160],
            )
            print(f"[{client.name}] warm fetch failed ({note[:120]}); click")
            clicked = _click_add_to_cart(wb.driver, timeout=3)
            if clicked:
                verified = self._verify(
                    client,
                    wb,
                    before,
                    "warm-click PLACE PRE-ORDER/CART",
                    poll_seconds=7.0,
                )
                if verified[0]:
                    return verified
                # Click happened — do not repark.
                return False, (
                    f"warm failed: fetch={note}; click=yes; cart unchanged"
                )

        if not _is_retryable_cart_note(note) and not clicked:
            return False, f"warm failed: fetch={note}; click=no button"

        # 4) Last resort: soft return to PDP once (no home hop), then one click + tiny burst.
        # Avoid home→fetch spam that caused the 501 death spiral in drops.
        product_url = self._product_url(product_code)
        try:
            print(f"[{client.name}] warm soft PDP refresh + click (no fetch spam)")
            logger.info("[warm] soft PDP refresh session=%s", client.name)
            wb.driver.get(product_url)
            _wait_for_product_ready(wb.driver, timeout=8)
            force_vue_member_refresh(wb.driver)
            clicked2 = _click_add_to_cart(wb.driver, timeout=4)
            if clicked2:
                verified = self._verify(
                    client,
                    wb,
                    before,
                    "warm-pdp-refresh-click",
                    poll_seconds=7.0,
                )
                if verified[0]:
                    return verified
            ok2, note2 = _page_fetch_add_to_cart_burst(
                wb.driver,
                area_item_no=area_item_no,
                qty=qty,
                csrf=csrf,
                attempts=3,
                gap=0.15,
            )
            if ok2:
                return self._verify(client, wb, before, f"warm-pdp-refresh-fetch {note2}")
            if _is_stock_or_business_cart_error(note2):
                return False, f"stock/preallocation hold: {note2}"
            note = f"{note}; pdp-refresh={note2}"
            clicked = clicked or clicked2
        except Exception as exc:  # noqa: BLE001
            note = f"{note}; pdp-refresh-error={exc}"
            log_exception(logger, f"warm soft PDP refresh failed session={client.name}", exc)

        return False, f"warm failed: fetch={note}; click={'yes' if clicked else 'no button'}"

    @staticmethod
    def _is_on_product_page(wb: WarmBrowser, product_code: str) -> bool:
        if wb.driver is None or not product_code:
            return False
        try:
            url = (wb.driver.current_url or "").lower()
            title = (wb.driver.title or "").lower()
            if "page not available" in title:
                return False
            return product_code.lower() in url
        except Exception:  # noqa: BLE001
            return False

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
            expected_session = ""
            for c in cookies:
                if str(c.get("name") or "").upper().startswith("SESSION"):
                    expected_session = str(c.get("value") or "")
                    break
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
            # Hard reload so document request carries SESSION (SSR USER_DATA + Vue).
            driver.get(home)
            time.sleep(1.2)
            after_session = session_cookie_value(driver)
            if expected_session and after_session and after_session != expected_session:
                logger.warning(
                    "[warm] %s SESSION overwritten by site after reload "
                    "(injected len=%s now len=%s) — re-injecting",
                    client.name,
                    len(expected_session),
                    len(after_session),
                )
                print(
                    f"[warm] {client.name}: SESSION overwritten by guest cookie; re-injecting"
                )
                _load_cookies_into_driver(driver, cookies)
                driver.get(home)
                time.sleep(1.2)
            auth = verify_browser_logged_in(driver, timeout=12.0)
            if not auth.get("ok"):
                _load_cookies_into_driver(driver, cookies)
                driver.get(home)
                time.sleep(1.2)
                auth = verify_browser_logged_in(driver, timeout=10.0)
            if auth.get("ok"):
                who = auth.get("email") or auth.get("member_id") or "yes"
                print(f"[warm] {client.name}: logged in OK ({who})")
                logger.info("[warm] login OK session=%s auth=%s", client.name, auth)
                force_vue_member_refresh(driver)
            else:
                print(
                    f"[warm] {client.name}: NOT logged in in browser "
                    f"(SESSION={auth.get('has_session')} reason={str(auth.get('reason'))[:100]})"
                )
                logger.warning("[warm] login verify failed session=%s auth=%s", client.name, auth)
                wb.last_error = f"browser not logged in: {auth.get('reason')}"

            driver.get(product_url)
            _wait_for_product_ready(driver, timeout=60)
            if auth.get("ok"):
                force_vue_member_refresh(driver)
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
                if auth.get("ok"):
                    force_vue_member_refresh(driver)
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
        *,
        poll_seconds: float = 2.5,
    ) -> tuple[bool, str]:
        deadline = time.time() + max(1.0, poll_seconds)
        after: Optional[int] = None
        while True:
            try:
                fresh = _driver_cookies(wb.driver)
                if fresh:
                    apply_cookies_to_client(client, fresh)
                    if wb.cookie_file:
                        Path(str(wb.cookie_file)).write_text(
                            json.dumps(fresh, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                try:
                    client.refresh_csrf(required=False)
                except Exception:  # noqa: BLE001
                    pass
                after = _safe_cart_count(client)
            except Exception as exc:  # noqa: BLE001
                logger.warning("warm cart verify failed session=%s: %s", client.name, exc)
                after = None

            if after is not None and before is not None and after > before:
                return True, f"{note} cart {before}->{after}"
            if after is not None and after > 0 and (before or 0) == 0:
                return True, f"{note} cart_count={after}"
            if time.time() >= deadline:
                break
            time.sleep(0.4)

        if after is not None and before is not None and after <= before:
            return False, f"{note} but cart unchanged {before}->{after}"
        return True, f"{note} (verify cart on site)"
