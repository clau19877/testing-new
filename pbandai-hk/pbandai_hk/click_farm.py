"""Guest click farm: N browsers click PLACE PRE-ORDER on a wall-clock schedule.

No login. Each instance parks on the PDP and clicks at second :00 of every minute
(or CLICK_AT_SECOND). Instances keep running after cart success.
On cart success, navigates to cart/checkout and posts the payment (or cart) URL
to Discord webhook, then returns to the PDP for the next minute mark.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib import request as urlrequest

from .browser_cart import _click_add_to_cart, _wait_for_product_ready
from .logging_utils import get_logger, log_exception
from .proxy_util import redact_proxy
from .session_login import _create_webdriver

if TYPE_CHECKING:
    from .config import Config

logger = get_logger("click_farm")

_HOOK_JS = """
(() => {
  if (window.__pbCartHooked) return true;
  window.__pbCartHooked = true;
  window.__pbCartLast = null;
  const record = (status, body) => {
    try {
      window.__pbCartLast = {
        status: Number(status) || 0,
        body: String(body || '').slice(0, 800),
        t: Date.now(),
      };
    } catch (e) {}
  };
  const origFetch = window.fetch;
  if (typeof origFetch === 'function') {
    window.fetch = function(input, init) {
      const url = String((input && input.url) || input || '');
      const p = origFetch.apply(this, arguments);
      if (url.indexOf('addToCart') !== -1 || url.indexOf('/api/cart/') !== -1) {
        p.then(async (resp) => {
          try {
            const text = await resp.clone().text();
            record(resp.status, text);
          } catch (e) { record(resp.status, ''); }
        }).catch(() => {});
      }
      return p;
    };
  }
  const XO = XMLHttpRequest.prototype.open;
  const XS = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__pbUrl = String(url || '');
    return XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    this.addEventListener('loadend', () => {
      try {
        const u = this.__pbUrl || '';
        if (u.indexOf('addToCart') !== -1 || u.indexOf('/api/cart/') !== -1) {
          record(this.status, this.responseText || '');
        }
      } catch (e) {}
    });
    return XS.apply(this, arguments);
  };
  return true;
})();
"""


@dataclass
class FarmBrowser:
    name: str
    proxy: str = ""
    driver: Any = None
    ready: bool = False
    last_error: str = ""
    clicks: int = 0
    success: bool = False
    payment_url: str = ""


@dataclass
class ClickFarm:
    config: "Config"
    product_code: str
    browsers: List[FarmBrowser] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _success_lock: threading.Lock = field(default_factory=threading.Lock)
    _successes: List[FarmBrowser] = field(default_factory=list)

    def prepare(self) -> int:
        """Open BROWSER_INSTANCES guest Chromes parked on the product page."""
        self.close()
        n = max(1, int(self.config.browser_instances))
        proxies = self._load_proxies(n)
        product_url = self._product_url()
        schedule = self._schedule_label()
        print(
            f"[farm] opening {n} guest browser(s) on {self.product_code} "
            f"({schedule}; keep going after cart)"
        )
        logger.info(
            "[farm] prepare instances=%s product=%s schedule=%s discord=%s stop_on_first=%s",
            n,
            self.product_code,
            schedule,
            "yes" if self.config.discord_webhook_url else "no",
            self.config.stop_on_first_cart,
        )

        # Launch sequentially to avoid ChromeDriver stampede; click loop is parallel.
        for i in range(n):
            name = f"inst{i + 1:02d}"
            proxy = proxies[i] if i < len(proxies) else ""
            wb = self._open_one(name, proxy, product_url)
            self.browsers.append(wb)

        ready = sum(1 for b in self.browsers if b.ready)
        print(f"[farm] ready={ready}/{n}")
        logger.info("[farm] ready=%s/%s", ready, n)
        return ready

    def run(self) -> List[FarmBrowser]:
        """Click forever (or until stop) — each ready browser on its own thread."""
        workers = [b for b in self.browsers if b.ready and b.driver is not None]
        if not workers:
            raise RuntimeError("click farm: no ready browsers")

        schedule = self._schedule_label()
        print(
            f"[farm] clicking PLACE PRE-ORDER {schedule} "
            f"across {len(workers)} instance(s) — keep going; Ctrl+C to stop"
        )
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futs = [pool.submit(self._worker_loop, wb) for wb in workers]
            try:
                for fut in futs:
                    fut.result()
            except KeyboardInterrupt:
                print("[farm] stop requested")
                self._stop.set()
                for fut in futs:
                    try:
                        fut.result(timeout=15)
                    except Exception:  # noqa: BLE001
                        pass
        return list(self._successes)

    def close(self) -> None:
        self._stop.set()
        for wb in self.browsers:
            if wb.driver is None:
                continue
            try:
                wb.driver.quit()
            except Exception as exc:  # noqa: BLE001
                log_exception(logger, f"farm quit failed {wb.name}", exc)
            wb.driver = None
            wb.ready = False
        self.browsers = []
        self._stop = threading.Event()

    def _schedule_label(self) -> str:
        at = int(self.config.click_at_second)
        if at >= 0:
            return f"at :{at:02d} every minute"
        return f"every {self.config.click_interval_seconds}s"

    def _wait_for_next_click(self) -> None:
        """Block until the next scheduled ATC time (minute :SS or interval)."""
        at = int(self.config.click_at_second)
        if at < 0:
            interval = max(0.5, float(self.config.click_interval_seconds))
            end = time.time() + interval
            while time.time() < end and not self._stop.is_set():
                time.sleep(min(0.2, end - time.time()))
            return

        # Wall-clock: fire when local second == CLICK_AT_SECOND (default :00).
        while not self._stop.is_set():
            now = time.time()
            # Target = next occurrence of minute + at seconds (Unix aligned).
            minute_start = now - (now % 60)
            target = minute_start + at
            if target <= now + 0.02:
                target += 60.0
            while time.time() < target and not self._stop.is_set():
                remaining = target - time.time()
                time.sleep(min(0.05, remaining) if remaining > 0 else 0)
            return

    def _worker_loop(self, wb: FarmBrowser) -> None:
        assert wb.driver is not None
        while not self._stop.is_set():
            self._wait_for_next_click()
            if self._stop.is_set():
                return
            try:
                self._ensure_hook(wb.driver)
                before = self._read_last(wb.driver)
                clicked = _click_add_to_cart(wb.driver, timeout=3)
                wb.clicks += 1
                if clicked:
                    stamp = time.strftime("%H:%M:%S")
                    print(f"[{wb.name}] {stamp} click #{wb.clicks} PLACE PRE-ORDER")
                    logger.info("[farm] %s click #%s at %s", wb.name, wb.clicks, stamp)
                    ok, note, payment = self._await_success(wb, before_ts=(before or {}).get("t") or 0)
                    if ok:
                        wb.success = True
                        wb.payment_url = payment
                        with self._success_lock:
                            self._successes.append(wb)
                        print(f"[{wb.name}] CART SUCCESS → {payment}")
                        logger.info("[farm] success session=%s payment=%s note=%s", wb.name, payment, note)
                        self._notify_discord(wb, payment, note)
                        if self.config.stop_on_first_cart:
                            self._stop.set()
                            return
                        # Keep going — already reparked on PDP inside _extract_payment_link.
                else:
                    logger.warning("[farm] %s no button (click #%s)", wb.name, wb.clicks)
            except Exception as exc:  # noqa: BLE001
                wb.last_error = str(exc)
                log_exception(logger, f"farm worker error {wb.name}", exc)
                print(f"[{wb.name}] error: {exc}")

    def _await_success(
        self,
        wb: FarmBrowser,
        *,
        before_ts: float,
    ) -> tuple[bool, str, str]:
        """Poll hooked addToCart response, then confirm via cart page."""
        driver = wb.driver
        assert driver is not None
        deadline = time.time() + 8.0
        last: Dict[str, Any] = {}
        while time.time() < deadline:
            last = self._read_last(driver) or {}
            ts = float(last.get("t") or 0)
            status = int(last.get("status") or 0)
            if ts > before_ts and status:
                body = str(last.get("body") or "")
                if 200 <= status < 300:
                    payment = self._extract_payment_link(wb)
                    return True, f"addToCart status={status}", payment
                if status == 409 or "preallocation" in body.lower() or "outofstock" in body.lower():
                    return False, f"stock hold status={status}", ""
                # 500/501/502 — not success; keep looping outer click interval
                return False, f"addToCart status={status}: {body[:120]}", ""
            time.sleep(0.25)

        # No network capture — check cart page for items as soft success.
        payment = self._extract_payment_link(wb, require_items=True)
        if payment:
            return True, "cart page has items (no XHR capture)", payment
        return False, "no addToCart response", ""

    def _extract_payment_link(self, wb: FarmBrowser, *, require_items: bool = False) -> str:
        driver = wb.driver
        assert driver is not None
        cart_url = f"{self.config.base_url}/{self.config.area_code}/cart"
        checkout_url = f"{self.config.base_url}/{self.config.area_code}/checkout"
        try:
            driver.get(cart_url)
            time.sleep(2.0)
            source = (driver.page_source or "").lower()
            if require_items:
                # Empty cart heuristics
                empty_markers = (
                    "your cart is empty",
                    "cart is empty",
                    "購物車內沒有商品",
                    "購物車是空",
                    "no items",
                )
                has_empty = any(m in source for m in empty_markers)
                # If clearly empty, fail
                if has_empty:
                    # Return to PDP for next clicks
                    try:
                        driver.get(self._product_url())
                        _wait_for_product_ready(driver, timeout=15)
                        self._ensure_hook(driver)
                    except Exception:  # noqa: BLE001
                        pass
                    return ""

            # Try checkout CTA
            clicked_checkout = self._click_checkout(driver)
            if clicked_checkout:
                time.sleep(2.5)
            url = (driver.current_url or "").strip()
            # Prefer Global-e / checkout / payment URLs
            low = url.lower()
            if any(x in low for x in ("global-e", "globale", "checkout", "payment", "pay.")):
                payment = url
            elif "/cart" in low:
                payment = url or cart_url
            else:
                payment = url or cart_url

            # Soft attempt checkout URL if still on cart
            if payment.rstrip("/").endswith("/cart"):
                try:
                    driver.get(checkout_url)
                    time.sleep(1.5)
                    cur = (driver.current_url or "").strip()
                    if cur and "page not available" not in (driver.title or "").lower():
                        payment = cur
                except Exception:  # noqa: BLE001
                    pass

            # Park back on PDP for continued farm (unless stopping)
            if not self.config.stop_on_first_cart:
                try:
                    driver.get(self._product_url())
                    _wait_for_product_ready(driver, timeout=15)
                    self._ensure_hook(driver)
                except Exception:  # noqa: BLE001
                    pass
            return payment or cart_url
        except Exception as exc:  # noqa: BLE001
            log_exception(logger, f"payment link extract failed {wb.name}", exc)
            return cart_url

    def _click_checkout(self, driver: Any) -> bool:
        from selenium.webdriver.common.by import By

        needles = [
            "CHECKOUT",
            "Checkout",
            "PROCEED TO CHECKOUT",
            "Proceed to checkout",
            "PLACE ORDER",
            "結帳",
            "去結帳",
            "前往結帳",
            "付款",
        ]
        try:
            buttons = driver.find_elements(By.TAG_NAME, "button")
            buttons += driver.find_elements(By.TAG_NAME, "a")
            for el in buttons:
                try:
                    if not el.is_displayed():
                        continue
                    label = (el.text or el.get_attribute("aria-label") or "").strip()
                    if not label:
                        continue
                    if any(n.lower() in label.lower() for n in needles):
                        driver.execute_script("arguments[0].click();", el)
                        logger.info("clicked checkout control text=%r", label[:80])
                        return True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return False

    def _notify_discord(self, wb: FarmBrowser, payment_url: str, note: str) -> None:
        webhook = (self.config.discord_webhook_url or "").strip()
        product_url = self._product_url()
        content = (
            f"**P-Bandai HK cart success** `{wb.name}`\n"
            f"Product: `{self.product_code}`\n"
            f"Product URL: {product_url}\n"
            f"Payment / cart link: {payment_url}\n"
            f"Note: {note}"
        )
        if not webhook:
            print(f"[farm] Discord webhook not set — payment link:\n{payment_url}")
            logger.warning("[farm] no DISCORD_WEBHOOK_URL; payment=%s", payment_url)
            return
        payload = {
            "content": content,
            "embeds": [
                {
                    "title": f"Cart OK — {self.product_code}",
                    "description": (
                        f"**Instance:** `{wb.name}`\n"
                        f"**Payment link:** {payment_url}\n"
                        f"**Product:** {product_url}"
                    ),
                    "color": 5763719,
                }
            ],
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urlrequest.Request(
                webhook,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "pbandai-hk-bot"},
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=15) as resp:
                logger.info("[farm] discord status=%s instance=%s", getattr(resp, "status", "?"), wb.name)
            print(f"[{wb.name}] Discord webhook sent")
        except Exception as exc:  # noqa: BLE001
            log_exception(logger, f"discord webhook failed {wb.name}", exc)
            print(f"[{wb.name}] Discord webhook error: {exc}")

    def _open_one(self, name: str, proxy: str, product_url: str) -> FarmBrowser:
        wb = FarmBrowser(name=name, proxy=proxy)
        driver = None
        try:
            print(f"[farm] start {name} proxy={redact_proxy(proxy) or '-'}")
            driver = _create_webdriver(self.config, proxy=proxy or "")
            try:
                driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {
                        "source": (
                            "Object.defineProperty(navigator, 'webdriver', "
                            "{get: () => undefined});"
                            + _HOOK_JS
                        )
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            home = f"{self.config.base_url}/{self.config.area_code}/"
            driver.get(home)
            time.sleep(1.0)
            driver.get(product_url)
            _wait_for_product_ready(driver, timeout=60)
            title = (driver.title or "").lower()
            if "page not available" in title:
                time.sleep(2.0)
                driver.get(product_url)
                _wait_for_product_ready(driver, timeout=40)
                title = (driver.title or "").lower()
            self._ensure_hook(driver)
            wb.driver = driver
            wb.ready = "page not available" not in title
            if not wb.ready:
                wb.last_error = "PDP page not available"
                print(f"[farm] {name} PDP unavailable")
            else:
                print(f"[farm] ready {name}")
        except Exception as exc:  # noqa: BLE001
            wb.ready = False
            wb.last_error = str(exc)
            log_exception(logger, f"farm open failed {name}", exc)
            print(f"[farm] failed {name}: {exc}")
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            wb.driver = None
        return wb

    def _product_url(self) -> str:
        return f"{self.config.base_url}/{self.config.area_code}/item/{self.product_code}"

    def _ensure_hook(self, driver: Any) -> None:
        try:
            driver.execute_script(_HOOK_JS)
        except Exception:  # noqa: BLE001
            pass

    def _read_last(self, driver: Any) -> Optional[Dict[str, Any]]:
        try:
            val = driver.execute_script("return window.__pbCartLast || null;")
            return val if isinstance(val, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def _load_proxies(self, n: int) -> List[str]:
        proxies: List[str] = []
        path = Path(self.config.proxy_csv)
        if path.exists():
            try:
                from .csv_tasks import load_proxies_csv

                proxies = load_proxies_csv(path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[farm] proxy.csv load failed: %s", exc)
        if not proxies and self.config.proxy_url:
            proxies = [self.config.proxy_url]
        if not proxies:
            return [""] * n
        # Round-robin fill to n
        out: List[str] = []
        for i in range(n):
            out.append(proxies[i % len(proxies)])
        return out
