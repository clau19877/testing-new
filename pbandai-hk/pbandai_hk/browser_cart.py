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

        # Seed domain, inject cookies (CDP), then hard-reload so Vue picks up SESSION.
        home = f"{config.base_url}/{config.area_code}/"
        driver.get(home)
        time.sleep(1.2)
        cookie_stats = _load_cookies_into_driver(driver, cookies)
        logger.info(
            "browser cookies applied session=%s total=%s applied=%s sessionCookies=%s failed=%s",
            client.name,
            cookie_stats.get("total"),
            cookie_stats.get("applied"),
            cookie_stats.get("session"),
            cookie_stats.get("failed"),
        )
        driver.get(home)
        time.sleep(1.2)
        auth = verify_browser_logged_in(driver, timeout=12.0)
        if not auth.get("ok"):
            # One retry: re-inject then reload.
            _load_cookies_into_driver(driver, cookies)
            driver.get(home)
            time.sleep(1.2)
            auth = verify_browser_logged_in(driver, timeout=10.0)
        if auth.get("ok"):
            print(
                f"[{client.name}] Browser login OK "
                f"(member={auth.get('member_id') or auth.get('email') or 'yes'})"
            )
            force_vue_member_refresh(driver)
        else:
            print(
                f"[{client.name}] Browser NOT logged in "
                f"(session={auth.get('has_session')} reason={auth.get('reason')})"
            )
            logger.warning(
                "browser login verify failed session=%s auth=%s",
                client.name,
                auth,
            )
        driver.get(product_url)
        _wait_for_product_ready(driver, timeout=60)
        if auth.get("ok"):
            force_vue_member_refresh(driver)
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
            ok, note = _page_fetch_add_to_cart_burst(
                driver,
                area_item_no=area_item_no,
                qty=qty,
                csrf=client.csrf_token or "",
                attempts=12,
                gap=0.12,
            )
            if not ok:
                return False, f"PDP down + {note}"
        elif not _click_add_to_cart(driver, timeout=45):
            ok, note = _page_fetch_add_to_cart_burst(
                driver,
                area_item_no=area_item_no,
                qty=qty,
                csrf=client.csrf_token or "",
                attempts=12,
                gap=0.12,
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


def _normalize_cookie_domain(domain: Any) -> str:
    text = str(domain or ".p-bandai.com").strip()
    if not text:
        return ".p-bandai.com"
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].strip()
    if text.startswith("www."):
        text = text[4:]
    if "p-bandai.com" in text and not text.startswith("."):
        text = "." + text.lstrip(".")
    return text or ".p-bandai.com"


def _cookie_expiry(cookie: Dict[str, Any]) -> Optional[float]:
    for key in ("expiry", "expires", "expirationDate"):
        raw = cookie.get(key)
        if raw in (None, "", 0, "0"):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        # Some dumps use milliseconds.
        if value > 10_000_000_000:
            value = value / 1000.0
        return value
    return None


def _load_cookies_into_driver(driver: Any, cookies: List[Dict[str, Any]]) -> Dict[str, int]:
    """Inject cookies via CDP first (reliable for httpOnly SESSION), then Selenium fallback."""
    stats = {"total": 0, "applied": 0, "failed": 0, "session": 0}
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:  # noqa: BLE001
        pass
    # Drop guest cookies from the first navigation so a site-issued SESSION
    # cannot shadow the real login SESSION we are about to inject.
    try:
        driver.execute_cdp_cmd("Network.clearBrowserCookies", {})
    except Exception:  # noqa: BLE001
        try:
            driver.delete_all_cookies()
        except Exception:  # noqa: BLE001
            pass

    base_urls = (
        "https://p-bandai.com/",
        "https://p-bandai.com/hk/",
        "https://www.p-bandai.com/",
    )

    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        if not name or value is None:
            continue
        stats["total"] += 1
        domain = _normalize_cookie_domain(cookie.get("domain"))
        path = str(cookie.get("path") or "/") or "/"
        secure = bool(cookie.get("secure", True))
        http_only = bool(cookie.get("httpOnly", name.upper().startswith("SESSION")))
        same_site = str(cookie.get("sameSite") or cookie.get("same_site") or "Lax").strip()
        expiry = _cookie_expiry(cookie)
        value_s = str(value)

        applied = False
        # Prefer URL-scoped CDP cookies — domain-only setCookie often lands in the
        # jar but is not sent on document/API requests in Chromium.
        for url in base_urls:
            cdp_payload: Dict[str, Any] = {
                "name": name,
                "value": value_s,
                "url": url,
                "path": path,
                "secure": secure,
                "httpOnly": http_only,
            }
            if expiry is not None:
                cdp_payload["expires"] = expiry
            if same_site:
                normalized = same_site[:1].upper() + same_site[1:].lower()
                if normalized.lower() == "none":
                    normalized = "None"
                cdp_payload["sameSite"] = normalized
            try:
                result = driver.execute_cdp_cmd("Network.setCookie", cdp_payload)
                if bool((result or {}).get("success", True)):
                    applied = True
            except Exception:  # noqa: BLE001
                continue

        if not applied:
            cdp_payload = {
                "name": name,
                "value": value_s,
                "domain": domain,
                "path": path,
                "secure": secure,
                "httpOnly": http_only,
            }
            if expiry is not None:
                cdp_payload["expires"] = expiry
            try:
                result = driver.execute_cdp_cmd("Network.setCookie", cdp_payload)
                applied = bool((result or {}).get("success", True))
            except Exception:  # noqa: BLE001
                applied = False

        if not applied:
            item: Dict[str, Any] = {
                "name": name,
                "value": value_s,
                "path": path,
                "domain": domain,
                "secure": secure,
            }
            if expiry is not None:
                item["expiry"] = int(expiry)
            try:
                driver.add_cookie(item)
                applied = True
            except Exception:  # noqa: BLE001
                try:
                    item.pop("domain", None)
                    driver.add_cookie(item)
                    applied = True
                except Exception:  # noqa: BLE001
                    applied = False

        if applied:
            stats["applied"] += 1
            if name.upper().startswith("SESSION"):
                stats["session"] += 1
        else:
            stats["failed"] += 1
    return stats


def session_cookie_value(driver: Any) -> str:
    try:
        result = driver.execute_cdp_cmd(
            "Network.getCookies",
            {"urls": ["https://p-bandai.com/", "https://p-bandai.com/hk/"]},
        )
        for cookie in result.get("cookies") or []:
            if str(cookie.get("name") or "").upper().startswith("SESSION"):
                return str(cookie.get("value") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        for cookie in driver.get_cookies():
            if str(cookie.get("name") or "").upper().startswith("SESSION"):
                return str(cookie.get("value") or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


def driver_has_session_cookie(driver: Any) -> bool:
    return bool(session_cookie_value(driver))


def verify_browser_logged_in(driver: Any, timeout: float = 20.0) -> Dict[str, Any]:
    """Confirm login via /api/context/member.loggedInMember (not csrfToken alone)."""
    deadline = time.time() + max(3.0, timeout)
    last: Dict[str, Any] = {"ok": False, "reason": "not-checked", "has_session": False}
    try:
        driver.set_script_timeout(12)
    except Exception:  # noqa: BLE001
        pass

    # Match site axios headers — area code matters for member context.
    script = """
        const callback = arguments[arguments.length - 1];
        const area = (location.pathname.split('/')[1] || 'hk').toLowerCase();
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 8000);
        fetch('/api/context/member', {
            method: 'GET',
            credentials: 'include',
            headers: {
                'Accept': 'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
                'X-G1-Area-Code': area,
            },
            signal: ctrl.signal,
        }).then(async (resp) => {
            clearTimeout(timer);
            const text = await resp.text();
            let json = null;
            try { json = JSON.parse(text); } catch (e) { json = null; }
            const member = json && json.loggedInMember ? json.loggedInMember : null;
            callback({
                status: resp.status,
                hasJson: !!json,
                keys: json ? Object.keys(json).slice(0, 12) : [],
                isLoggedIn: !!(member && member.isLoggedIn),
                member: member,
                csrf: !!(json && json.csrfToken),
                bodyPreview: String(text || '').slice(0, 180),
            });
        }).catch((err) => {
            clearTimeout(timer);
            callback({ status: 0, hasJson: false, isLoggedIn: false, error: String(err) });
        });
    """
    while time.time() < deadline:
        has_session = driver_has_session_cookie(driver)
        try:
            payload = driver.execute_async_script(script)
        except Exception as exc:  # noqa: BLE001
            last = {"ok": False, "reason": f"script-error:{exc}", "has_session": has_session}
            time.sleep(0.8)
            continue

        status = int((payload or {}).get("status") or 0) if isinstance(payload, dict) else 0
        is_logged_in = bool((payload or {}).get("isLoggedIn")) if isinstance(payload, dict) else False
        member = (payload or {}).get("member") if isinstance(payload, dict) else None
        member_id = ""
        email = ""
        if isinstance(member, dict):
            member_id = str(
                member.get("memberId")
                or member.get("memberNo")
                or member.get("dwhLinkageNo")
                or member.get("loginId")
                or member.get("id")
                or ""
            ).strip()
            email = str(
                member.get("email")
                or member.get("mailAddress")
                or member.get("emailAddress")
                or ""
            ).strip()
        if is_logged_in and has_session:
            return {
                "ok": True,
                "has_session": True,
                "member_id": member_id or "logged-in",
                "email": email,
                "status": status,
            }
        keys = (payload or {}).get("keys") if isinstance(payload, dict) else []
        last = {
            "ok": False,
            "has_session": has_session,
            "status": status,
            "keys": keys,
            "reason": (payload or {}).get("error")
            or (
                "guest-context (SESSION present but no loggedInMember — cookie not sent/accepted)"
                if has_session and status == 200
                else (payload or {}).get("bodyPreview") or "member-context-empty"
            ),
        }
        time.sleep(0.8)
    return last


def force_vue_member_refresh(driver: Any) -> None:
    """Nudge SPA header: site boots from window.USER_DATA and may not refreshMember()."""
    script = """
        const callback = arguments[arguments.length - 1];
        const area = (location.pathname.split('/')[1] || 'hk').toLowerCase();
        fetch('/api/context/member', {
            method: 'GET',
            credentials: 'include',
            headers: {
                'Accept': 'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
                'X-G1-Area-Code': area,
            },
        }).then(async (resp) => {
            const json = await resp.json().catch(() => null);
            if (json) {
                try { window.USER_DATA = Object.assign({}, window.USER_DATA || {}, json); } catch (e) {}
            }
            // Soft UI hint: replace Sign In link text if member logged in.
            const logged = !!(json && json.loggedInMember && json.loggedInMember.isLoggedIn);
            callback({ ok: logged, keys: json ? Object.keys(json).slice(0, 8) : [] });
        }).catch((err) => callback({ ok: false, error: String(err) }));
    """
    try:
        driver.set_script_timeout(12)
        driver.execute_async_script(script)
    except Exception:  # noqa: BLE001
        pass


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


def _is_stock_or_business_cart_error(note: str) -> bool:
    """Bandai maps Preallocation to the same UI as out-of-stock."""
    text = (note or "").lower()
    needles = (
        "status=409",
        " 409:",
        "409:",
        "preallocation",
        "outofstock",
        "out_of_stock",
        "exceededmaxpurchase",
        "couldnotaddtocartbyoutofstock",
        "couldnotaddtocartbypreallocation",
        "couldnotaddtocartbyendofsale",
        "couldnotaddtocartbysuspendeditem",
        "couldnotaddtocartbymaxpurchaseqty",
        "couldnotaddtocartbyminpurchaseqty",
    )
    return any(n in text for n in needles)


def _is_retryable_cart_note(note: str) -> bool:
    if _is_stock_or_business_cart_error(note):
        return False
    text = (note or "").lower()
    needles = (
        "503",
        "502",
        "500",
        "504",
        "429",
        "html error",
        "page not available",
        "timeout",
        "timed out",
        "abort",
        "connection",
        "click=no button",
    )
    return any(n in text for n in needles)


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
        const area = (location.pathname.split('/')[1] || 'hk').toLowerCase();
        let csrf = csrfArg || '';
        // Refresh CSRF from member context when possible (stale token can fail cart).
        try {
          const m = await fetch('/api/context/member', {
            method: 'GET',
            credentials: 'include',
            headers: {
              'Accept': 'application/json, text/plain, */*',
              'X-Requested-With': 'XMLHttpRequest',
              'X-G1-Area-Code': area,
            },
          });
          const mj = await m.json().catch(() => null);
          if (mj && mj.csrfToken) csrf = mj.csrfToken;
        } catch (e) {}
        if (!csrf) {
          csrf =
            document.querySelector('meta[name="csrf-token"]')?.content ||
            document.querySelector('meta[name="_csrf"]')?.content ||
            document.querySelector('input[name="_csrf"]')?.value ||
            '';
        }
        const headers = {
          'Content-Type': 'application/json',
          'Accept': 'application/json, text/plain, */*',
          'X-Requested-With': 'XMLHttpRequest',
          'X-G1-Area-Code': area,
          'Origin': location.origin,
          'Referer': location.href,
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
        callback({status: resp.status, body: text.slice(0, 800), csrf: !!csrf});
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


def _page_fetch_add_to_cart_burst(
    driver: Any,
    *,
    area_item_no: str,
    qty: int,
    csrf: str,
    attempts: int = 12,
    gap: float = 0.12,
    max_waf_hits: int = 4,
) -> tuple[bool, str]:
    """Short retry on gateway errors; abort early on repeated WAF 501 HTML.

    Raw fetch often lacks Shape/F5 tokens — hammering 501 pages makes WAF worse.
    Prefer native button click (warm/browser paths) over long fetch bursts.
    """
    last = "browser-fetch not attempted"
    waf_hits = 0
    for i in range(1, max(1, attempts) + 1):
        ok, note = _page_fetch_add_to_cart(
            driver, area_item_no=area_item_no, qty=qty, csrf=csrf
        )
        if ok:
            return True, f"{note} (burst {i}/{attempts})"
        last = note
        if not _is_retryable_cart_note(note):
            return False, note
        low = note.lower()
        if "501" in low or "page not available" in low or "html error" in low:
            waf_hits += 1
            if waf_hits >= max(1, max_waf_hits):
                return False, last
        if i < attempts:
            time.sleep(gap)
    return False, last


def _page_has_error(driver: Any) -> bool:
    try:
        text = (driver.page_source or "").lower()
        return "page not available" in text or "unable to add" in text
    except Exception:  # noqa: BLE001
        return False


def _short_error(text: str) -> str:
    low = text.lower()
    if "couldnotaddtocartbypreallocation" in low.replace(" ", ""):
        return "409 CouldNotAddToCartByPreallocation (stock held/OOS)"
    if "couldnotaddtocartbyoutofstock" in low.replace(" ", ""):
        return "409 CouldNotAddToCartByOutOfStock"
    if "<html" in low or "<!doctype" in low:
        if "page not available" in low:
            return "HTML error page (WAF/501 Page not available)"
        return f"HTML error page ({len(text)} chars)"
    return text[:240]
