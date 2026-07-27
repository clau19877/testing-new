from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .logging_utils import get_logger, log_exception
from .proxy_util import redact_proxy
from .session_login import _create_webdriver, _driver_cookies

if TYPE_CHECKING:
    from .api import PBandaiHkClient
    from .config import Config

logger = get_logger("browser_cart")


def browser_add_to_cart(
    config: "Config",
    client: "PBandaiHkClient",
    *,
    product_code: str,
    area_item_no: str = "",
    qty: int = 1,
    proxy: str = "",
    cookie_file: str | Path | None = None,
) -> tuple[bool, str]:
    """Add to cart by driving a real browser (bypasses API WAF 501 on POST)."""
    from .sessions import apply_cookies_to_client, load_cookies_file

    product_url = f"{config.base_url}/{config.area_code}/item/{product_code}"
    effective_proxy = proxy or client.proxy or config.proxy_url or ""
    before = _safe_cart_count(client)

    cookies: List[Dict[str, Any]] = []
    if cookie_file and Path(str(cookie_file)).exists():
        cookies = load_cookies_file(Path(str(cookie_file)))
    elif client.session.cookies:
        for c in client.session.cookies:
            cookies.append(
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain or ".p-bandai.com",
                    "path": c.path or "/",
                }
            )

    print(
        f"[{client.name}] Browser add-to-cart {product_code} "
        f"(proxy={redact_proxy(effective_proxy) or '-'})"
    )
    # Prefer headed browser; headless often hits "Page not available".
    original_bg = bool(config.background_mode)
    config.background_mode = False
    driver = None
    try:
        driver = _create_webdriver(config, proxy=effective_proxy)
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

        # Seed domain before adding cookies.
        driver.get(f"{config.base_url}/{config.area_code}/")
        time.sleep(1.5)
        _load_cookies_into_driver(driver, cookies)
        driver.get(product_url)
        _wait_for_product_ready(driver, timeout=60)
        logger.info(
            "browser cart opened session=%s url=%s title=%s proxy=%s",
            client.name,
            driver.current_url,
            (driver.title or "")[:120],
            redact_proxy(effective_proxy) or "-",
        )
        pdp_down = "page not available" in (driver.title or "").lower()
        if pdp_down:
            logger.warning(
                "browser PDP unavailable session=%s; trying in-page fetch from HK home",
                client.name,
            )
            driver.get(f"{config.base_url}/{config.area_code}/")
            time.sleep(1.2)
            if not area_item_no:
                return False, "browser got PAGE NOT AVAILABLE and no areaItemNo"
            ok, note = _page_fetch_add_to_cart(
                driver,
                area_item_no=area_item_no,
                qty=qty,
                csrf=client.csrf_token or "",
            )
            if not ok:
                return False, f"PDP down + {note}"
        elif not _click_add_to_cart(driver, timeout=45):
            ok, note = _page_fetch_add_to_cart(
                driver,
                area_item_no=area_item_no,
                qty=qty,
                csrf=client.csrf_token or "",
            )
            if not ok:
                return False, note
        else:
            time.sleep(2.5)

        # Transfer refreshed cookies back to API client for verification.
        fresh = _driver_cookies(driver)
        if fresh:
            apply_cookies_to_client(client, fresh)
            if cookie_file:
                Path(str(cookie_file)).write_text(
                    json.dumps(fresh, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        try:
            client.refresh_csrf()
            after = _safe_cart_count(client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cart summary refresh failed: %s", exc)
            after = None

        if after is not None and before is not None and after > before:
            return True, f"browser-added cart {before}->{after}"
        if after is not None and after > 0 and (before or 0) == 0:
            return True, f"browser-added cart_count={after}"
        if after is not None and before is not None and after <= before:
            return False, f"browser click/fetch but cart unchanged {before}->{after}"
        if _page_has_error(driver):
            return False, "browser add-to-cart showed an error on page"
        # Detect login wall.
        try:
            if "sign in" in (driver.page_source or "").lower() and after == before:
                return False, "browser still on sign-in / cart unchanged"
        except Exception:  # noqa: BLE001
            pass
        return True, "browser pre-order/cart clicked (verify cart on site)"
    finally:
        config.background_mode = original_bg
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:  # noqa: BLE001
                log_exception(logger, "driver.quit failed", exc)


def _wait_for_product_ready(driver: Any, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = (driver.title or "").lower()
        if "page not available" in title:
            time.sleep(1.0)
            continue
        try:
            source = driver.page_source or ""
            if (
                "PLACE PRE-ORDER" in source
                or "ADD TO CART" in source
                or "加入購物車" in source
                or "c-input-quantity" in source
            ):
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)


def _safe_cart_count(client: "PBandaiHkClient") -> Optional[int]:
    try:
        summary = client.cart_summary()
        return int(summary.get("totalItemCount") or 0)
    except Exception:  # noqa: BLE001
        return None


def _load_cookies_into_driver(driver: Any, cookies: List[Dict[str, Any]]) -> None:
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name:
            continue
        item = {
            "name": name,
            "value": value,
            "path": cookie.get("path") or "/",
        }
        domain = cookie.get("domain") or ".p-bandai.com"
        # Selenium wants domain without leading scheme; keep leading dot when present.
        item["domain"] = domain
        try:
            driver.add_cookie(item)
        except Exception:  # noqa: BLE001
            # Retry without domain if rejected.
            try:
                item.pop("domain", None)
                driver.add_cookie(item)
            except Exception:
                continue


def _click_add_to_cart(driver: Any, timeout: int = 45) -> bool:
    from selenium.webdriver.common.by import By

    deadline = time.time() + timeout
    text_needles = [
        "PLACE PRE-ORDER",
        "Place Pre-Order",
        "PLACE ORDER",
        "Place Order",
        "ADD TO CART",
        "Add to cart",
        "加入購物車",
        "カートに追加",
        "預購",
        "立即預訂",
    ]
    css_candidates = [
        "button[class*='cart']",
        "button[class*='Cart']",
        "button[type='button']",
        "button",
    ]
    _ = css_candidates  # reserved for future strict selectors
    while time.time() < deadline:
        # Prefer buttons by visible text.
        try:
            buttons = driver.find_elements(By.TAG_NAME, "button")
            for btn in buttons:
                try:
                    if not btn.is_displayed() or not btn.is_enabled():
                        continue
                    label = (btn.text or btn.get_attribute("aria-label") or "").strip()
                    if any(n.lower() in label.lower() for n in text_needles):
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        time.sleep(0.3)
                        try:
                            btn.click()
                        except Exception:  # noqa: BLE001
                            driver.execute_script("arguments[0].click();", btn)
                        logger.info("clicked add-to-cart button text=%r", label[:80])
                        return True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

        # XPath contains text.
        for needle in text_needles:
            try:
                xpath = (
                    f"//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz', "
                    f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), '{needle.upper()}')]"
                )
                els = driver.find_elements(By.XPATH, xpath)
                for el in els:
                    if el.is_displayed() and el.is_enabled():
                        driver.execute_script("arguments[0].click();", el)
                        logger.info("clicked add-to-cart xpath needle=%s", needle)
                        return True
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.5)
    logger.warning("could not find add-to-cart button within %ss", timeout)
    return False


def _page_fetch_add_to_cart(
    driver: Any,
    *,
    area_item_no: str,
    qty: int,
    csrf: str,
) -> tuple[bool, str]:
    if not area_item_no:
        return False, "browser fallback: no areaItemNo for in-page fetch"
    script = """
    const areaItemNo = arguments[0];
    const qty = arguments[1];
    const csrfArg = arguments[2];
    const callback = arguments[arguments.length - 1];
    (async () => {
      try {
        const csrf =
          csrfArg ||
          document.querySelector('meta[name="csrf-token"]')?.content ||
          document.querySelector('meta[name="_csrf"]')?.content ||
          document.querySelector('input[name="_csrf"]')?.value ||
          '';
        const headers = {
          'Content-Type': 'application/json',
          'Accept': 'application/json, text/plain, */*',
          'X-Requested-With': 'XMLHttpRequest',
          'X-G1-Area-Code': 'hk',
        };
        if (csrf) {
          headers['X-CSRF-TOKEN'] = csrf;
          headers['X-XSRF-TOKEN'] = csrf;
        }
        // Native fetch so F5/Shape page hooks can inject bot tokens.
        const resp = await fetch('/api/cart/addToCart', {
          method: 'POST',
          credentials: 'include',
          headers,
          body: JSON.stringify([{areaItemNo, qty}]),
        });
        const text = await resp.text();
        callback({status: resp.status, body: text.slice(0, 800)});
      } catch (err) {
        callback({status: 0, body: String(err)});
      }
    })();
    """
    try:
        driver.set_script_timeout(30)
        result = driver.execute_async_script(script, area_item_no, int(qty), csrf or "")
        status = int((result or {}).get("status") or 0)
        body = str((result or {}).get("body") or "")
        logger.info("in-page fetch addToCart status=%s body=%s", status, body[:200])
        if 200 <= status < 300:
            return True, f"browser-fetch added status={status}"
        return False, f"browser-fetch addToCart failed status={status}: {_short_error(body)}"
    except Exception as exc:  # noqa: BLE001
        log_exception(logger, "in-page fetch addToCart failed", exc)
        return False, f"browser-fetch error: {exc}"


def _page_has_error(driver: Any) -> bool:
    try:
        text = (driver.page_source or "").lower()
        return "page not available" in text or "unable to add" in text
    except Exception:  # noqa: BLE001
        return False


def _short_error(text: str) -> str:
    low = text.lower()
    if "<html" in low or "<!doctype" in low:
        if "page not available" in low:
            return "HTML error page (WAF/501 Page not available)"
        return f"HTML error page ({len(text)} chars)"
    return text[:240]
