"""Guest click farm: N browsers click PLACE PRE-ORDER on a wall-clock schedule.

No login. Each instance parks on the PDP and clicks at second :00 of every minute
(or CLICK_AT_SECOND). Instances keep running after cart success.
On cart success, navigates to cart/checkout and posts the payment (or cart) URL
to Discord webhook, then returns to the PDP for the next minute mark.
"""

from __future__ import annotations

import json
import random
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
from .session_login import _create_webdriver, build_browser_identity

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
    _last_oos_refresh: float = 0.0
    _last_oos_log: float = 0.0
    _last_idle_activity: float = 0.0
    _last_idle_settle: float = 0.0
    _oos_refresh_due: float = 0.0  # per-instance jittered interval


@dataclass
class ClickFarm:
    config: "Config"
    product_code: str
    browsers: List[FarmBrowser] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _success_lock: threading.Lock = field(default_factory=threading.Lock)
    _browsers_lock: threading.Lock = field(default_factory=threading.Lock)
    _successes: List[FarmBrowser] = field(default_factory=list)
    _plan: List[tuple[str, str]] = field(default_factory=list)  # (name, proxy)
    _pdp_gate: Optional[threading.Semaphore] = field(default=None, repr=False)

    def prepare(self) -> int:
        """Plan N independent workers. Does NOT open browsers (no global wait)."""
        self.close()
        n = max(1, int(self.config.browser_instances))
        proxies = self._load_proxies(n)
        schedule = self._schedule_label()
        self._plan = [
            (f"inst{i + 1:02d}", proxies[i] if i < len(proxies) else "")
            for i in range(n)
        ]
        self._pdp_gate = threading.Semaphore(max(1, int(self.config.pdp_max_concurrent)))
        print(
            f"[farm] planned {n} independent instance(s) on {self.product_code} "
            f"({schedule}; each opens + clicks on its own thread)"
        )
        logger.info(
            "[farm] prepare planned=%s product=%s schedule=%s discord=%s "
            "independent=yes stop_on_first=%s pdp_max_concurrent=%s",
            n,
            self.product_code,
            schedule,
            "yes" if self.config.discord_webhook_url else "no",
            self.config.stop_on_first_cart,
            self.config.pdp_max_concurrent,
        )
        stagger = max(0.0, float(self.config.open_stagger_seconds))
        idle = float(self.config.idle_activity_seconds)
        idle_label = f"every ~{idle:.0f}s" if idle > 0 else "off"
        proxy_count = len({p for _, p in self._plan if p})
        if proxy_count and proxy_count < n:
            print(
                f"[farm] WARNING: only {proxy_count} unique proxy(ies) for {n} instances "
                "(shared egress increases PAGE NOT AVAILABLE / WAF)"
            )
        uniq = "on" if bool(getattr(self.config, "unique_browser_profiles", True)) else "off"
        warm = max(0.0, float(getattr(self.config, "home_warmup_seconds", 0.0) or 0.0))
        print(
            f"[farm] open stagger={stagger}s (+jitter) · launch+PDP staggered · "
            f"PDP retries={self.config.open_pdp_retries} · "
            f"PDP concurrent≤{self.config.pdp_max_concurrent} · "
            f"OOS refresh~{self.config.oos_refresh_seconds:.0f}s · "
            f"idle={idle_label} · unique profiles={uniq} · home warm~{warm:.0f}s · "
            f"no wait for other instances"
        )
        return n

    def run(self) -> List[FarmBrowser]:
        """Each instance opens Chrome then enters the click loop independently."""
        if not self._plan:
            raise RuntimeError("click farm: prepare() was not called")

        n = len(self._plan)
        schedule = self._schedule_label()
        print(
            f"[farm] starting {n} independent worker(s) — "
            f"PLACE PRE-ORDER {schedule}; Ctrl+C to stop"
        )
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = [
                pool.submit(self._instance_lifecycle, idx, name, proxy)
                for idx, (name, proxy) in enumerate(self._plan)
            ]
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

    def _instance_lifecycle(self, index: int, name: str, proxy: str) -> None:
        """Open this instance, then click forever — never waits on other instances."""
        if self._stop.is_set():
            return
        product_url = self._product_url()
        print(f"[{name}] independent start")
        wb = self._open_one(name, proxy, product_url, index=index)
        with self._browsers_lock:
            self.browsers.append(wb)
        if wb.driver is None:
            print(f"[{name}] Chrome failed — instance exit")
            return
        print(f"[{name}] open done ready={wb.ready} — entering click loop now")
        logger.info("[farm] %s open done ready=%s — click loop", name, wb.ready)
        self._worker_loop(wb)

    def close(self) -> None:
        self._stop.set()
        with self._browsers_lock:
            browsers = list(self.browsers)
            self.browsers = []
        for wb in browsers:
            if wb.driver is None:
                continue
            try:
                wb.driver.quit()
            except Exception as exc:  # noqa: BLE001
                log_exception(logger, f"farm quit failed {wb.name}", exc)
            wb.driver = None
            wb.ready = False
        self._plan = []
        self._stop = threading.Event()
        self._pdp_gate = None

    def _acquire_pdp_slot(self, name: str) -> bool:
        """Block until a PDP navigation slot is free (limits origin stampede)."""
        gate = self._pdp_gate
        if gate is None:
            return True
        while not self._stop.is_set():
            if gate.acquire(blocking=True, timeout=0.25):
                return True
        return False

    def _release_pdp_slot(self) -> None:
        gate = self._pdp_gate
        if gate is None:
            return
        try:
            gate.release()
        except ValueError:
            pass

    def _oos_interval_for(self, wb: FarmBrowser) -> float:
        """Per-instance jittered refresh interval so heals don't lockstep."""
        base = float(self.config.oos_refresh_seconds)
        if base <= 0:
            return 0.0
        due = float(getattr(wb, "_oos_refresh_due", 0.0) or 0.0)
        if due <= 0:
            # Spread 0.7x..1.6x of base across instances.
            due = base * random.uniform(0.7, 1.6) + random.uniform(0.0, 4.0)
            wb._oos_refresh_due = due
        return due

    def _schedule_label(self) -> str:
        at = int(self.config.click_at_second)
        if at >= 0:
            return f"at :{at:02d} every minute"
        return f"every {self.config.click_interval_seconds}s"

    def _wait_for_next_click(self, wb: Optional[FarmBrowser] = None) -> None:
        """Block until the next scheduled ATC time; idle + OOS refresh while waiting."""
        at = int(self.config.click_at_second)
        if at < 0:
            interval = max(0.5, float(self.config.click_interval_seconds))
            end = time.time() + interval
            while time.time() < end and not self._stop.is_set():
                if wb is not None:
                    self._maybe_refresh_oos_while_waiting(wb)
                    self._maybe_human_idle(wb, seconds_until_click=end - time.time())
                time.sleep(max(0.0, min(0.2, end - time.time())))
            return

        # Wall-clock: fire when local second == CLICK_AT_SECOND (default :00).
        while not self._stop.is_set():
            now = time.time()
            minute_start = now - (now % 60)
            target = minute_start + at
            if target <= now + 0.02:
                target += 60.0
            while time.time() < target and not self._stop.is_set():
                if wb is not None:
                    remaining = target - time.time()
                    self._maybe_refresh_oos_while_waiting(wb)
                    self._maybe_human_idle(wb, seconds_until_click=remaining)
                remaining = target - time.time()
                time.sleep(max(0.0, min(0.25, remaining)))
            return

    def _maybe_human_idle(self, wb: FarmBrowser, *, seconds_until_click: float) -> None:
        """Scroll / blank-click to keep the browser session looking active."""
        interval = float(self.config.idle_activity_seconds)
        if interval <= 0 or wb.driver is None:
            return
        # Settle near top in the last few seconds so ATC stays on-screen.
        if 2.0 <= seconds_until_click < 4.5:
            last_settle = float(getattr(wb, "_last_idle_settle", 0.0) or 0.0)
            if time.time() - last_settle > 30.0:
                wb._last_idle_settle = time.time()
                try:
                    wb.driver.execute_script(
                        "window.scrollTo({top: 0, left: 0, behavior: 'smooth'});"
                    )
                except Exception:  # noqa: BLE001
                    pass
            return
        # Don't fidget in the last ~2s before ATC.
        if seconds_until_click < 2.0:
            return
        now = time.time()
        last = float(getattr(wb, "_last_idle_activity", 0.0) or 0.0)
        # Jitter so 20 instances don't all scroll in lockstep.
        due = interval + random.uniform(-1.5, 2.5)
        if now - last < max(3.0, due):
            return
        wb._last_idle_activity = now
        try:
            self._do_human_idle(wb.driver)
            logger.info("[farm] %s idle activity (scroll + blank-click)", wb.name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[farm] %s idle activity soft-fail: %s", wb.name, exc)

    def _do_human_idle(self, driver: Any) -> None:
        """Human-like wait activity: page scroll + blank-space click (keeps session warm)."""
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.by import By

        # Always scroll + blank-click (user request); optional mouse wiggle.
        actions = ["scroll", "blank_click"]
        if random.random() < 0.55:
            actions.append("mouse_wiggle")
        random.shuffle(actions)
        for action in actions:
            if action == "scroll":
                delta = random.choice([-420, -280, -160, 160, 280, 420, 560])
                driver.execute_script(
                    "window.scrollBy({top: arguments[0], left: 0, behavior: 'smooth'});",
                    delta,
                )
                time.sleep(random.uniform(0.15, 0.45))
                # Often scroll back partway so ATC stays reachable.
                if random.random() < 0.55:
                    driver.execute_script(
                        "window.scrollBy({top: arguments[0], left: 0, behavior: 'smooth'});",
                        -int(delta * 0.6),
                    )
                    time.sleep(random.uniform(0.1, 0.3))
            elif action == "blank_click":
                # Click a safe blank area (body / main), never cart buttons.
                target = None
                for sel in ("main", "#app", "body"):
                    try:
                        els = driver.find_elements(By.CSS_SELECTOR, sel)
                        if els and els[0].is_displayed():
                            target = els[0]
                            break
                    except Exception:  # noqa: BLE001
                        continue
                if target is None:
                    continue
                try:
                    size = target.size or {}
                    width = max(40, int(size.get("width") or 400))
                    height = max(40, int(size.get("height") or 300))
                    # Prefer upper/side margins away from the ATC column.
                    ox = random.randint(12, max(13, min(120, width // 4)))
                    oy = random.randint(40, max(41, min(220, height // 3)))
                    ActionChains(driver).move_to_element_with_offset(
                        target, ox, oy
                    ).click().perform()
                except Exception:  # noqa: BLE001
                    # Fallback: JS click blank coords (no navigation).
                    driver.execute_script(
                        "var e=document.elementFromPoint(24, 120);"
                        "if(e){e.dispatchEvent(new MouseEvent('click',"
                        "{bubbles:true,cancelable:true,view:window}));}"
                    )
                time.sleep(random.uniform(0.1, 0.35))
            elif action == "mouse_wiggle":
                try:
                    body = driver.find_element(By.TAG_NAME, "body")
                    chain = ActionChains(driver).move_to_element_with_offset(body, 40, 80)
                    for _ in range(random.randint(2, 4)):
                        chain = chain.move_by_offset(
                            random.randint(-30, 30),
                            random.randint(-20, 20),
                        )
                    chain.perform()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(random.uniform(0.08, 0.25))

    def _maybe_refresh_oos_while_waiting(self, wb: FarmBrowser) -> None:
        """Soft OOS / PNA: hard-refresh PDP on a jittered interval while parked."""
        interval = self._oos_interval_for(wb)
        if interval <= 0 or wb.driver is None:
            return
        last = float(getattr(wb, "_last_oos_refresh", 0.0) or 0.0)
        now = time.time()
        if now - last < interval:
            return
        try:
            if self._shows_out_of_stock(wb.driver) or self._page_looks_bad(wb.driver):
                wb._last_oos_refresh = now
                # Resample interval so the next heal doesn't stay phase-locked.
                wb._oos_refresh_due = 0.0
                last_log = float(getattr(wb, "_last_oos_log", 0.0) or 0.0)
                if now - last_log >= 60.0:
                    wb._last_oos_log = now
                    print(
                        f"[{wb.name}] still OOS/bad — refreshing ~every "
                        f"{float(self.config.oos_refresh_seconds):.0f}s "
                        f"(jittered, max {self.config.pdp_max_concurrent} concurrent)"
                    )
                logger.info("[farm] %s OOS refresh while waiting", wb.name)
                self._hard_refresh_pdp(wb)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[farm] %s OOS wait-refresh failed: %s", wb.name, exc)

    def _worker_loop(self, wb: FarmBrowser) -> None:
        assert wb.driver is not None
        # After open PNA, one gated multi-retry recover (staggered) — not every :00.
        if not wb.ready and not self._stop.is_set():
            # Extra jitter so recoveries don't align when many open failures finish together.
            time.sleep(random.uniform(0.5, 4.0))
            print(f"[{wb.name}] recovering PDP after open failure…")
            if self._recover_pdp(wb):
                print(f"[{wb.name}] PDP recovered")
            else:
                print(f"[{wb.name}] PDP still bad — will heal on jittered interval")
        while not self._stop.is_set():
            self._wait_for_next_click(wb)
            if self._stop.is_set():
                return
            try:
                # Soft OOS/500 is already refreshed on a jittered interval while
                # waiting. At :00 do at most ONE hard refresh (not multi-retry),
                # then click or skip — avoids a 20× recover stampede on the minute.
                if (
                    self._page_looks_bad(wb.driver)
                    or self._shows_out_of_stock(wb.driver)
                    or not wb.ready
                ):
                    last = float(getattr(wb, "_last_oos_refresh", 0.0) or 0.0)
                    # If we refreshed recently while waiting, don't hit origin again at :00.
                    if time.time() - last < 8.0 and not self._page_looks_bad(wb.driver):
                        if self._shows_out_of_stock(wb.driver):
                            print(f"[{wb.name}] still OOS after recent refresh — skip this minute")
                            continue
                    print(f"[{wb.name}] PDP OOS/bad at click time — one hard refresh")
                    if not self._hard_refresh_pdp(wb):
                        print(f"[{wb.name}] still OOS/unavailable — skip this minute")
                        continue
                    wb.ready = True

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
                else:
                    logger.warning("[farm] %s no button (click #%s)", wb.name, wb.clicks)
                    if self._shows_out_of_stock(wb.driver):
                        print(
                            f"[{wb.name}] page shows OUT OF STOCK "
                            "(no enabled PLACE PRE-ORDER) — refresh & wait next :00"
                        )
                        self._hard_refresh_pdp(wb)
                    elif self._page_looks_bad(wb.driver):
                        print(f"[{wb.name}] no ATC button (bad PDP) — one refresh for next round")
                        self._hard_refresh_pdp(wb)
                    else:
                        print(
                            f"[{wb.name}] no enabled ATC button "
                            "(not OOS text / not error page) — soft refresh"
                        )
                        self._hard_refresh_pdp(wb)
            except Exception as exc:  # noqa: BLE001
                wb.last_error = str(exc)
                log_exception(logger, f"farm worker error {wb.name}", exc)
                print(f"[{wb.name}] error: {exc}")
                try:
                    self._hard_refresh_pdp(wb)
                except Exception:  # noqa: BLE001
                    pass

    def _hard_refresh_pdp(self, wb: FarmBrowser) -> bool:
        driver = wb.driver
        if driver is None:
            return False
        product_url = self._product_url()
        home = f"{self.config.base_url}/{self.config.area_code}/"
        if not self._acquire_pdp_slot(wb.name):
            return False
        try:
            # Bypass HTTP cache where possible.
            try:
                driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
            except Exception:  # noqa: BLE001
                pass
            # Bust SPA/CDN soft-cache with a no-op query once, then clean URL.
            bust = f"{product_url}?_={int(time.time() * 1000)}"
            driver.get(bust)
            _wait_for_product_ready(driver, timeout=25)
            if self._shows_out_of_stock(driver) or self._page_looks_bad(driver):
                driver.get(home)
                time.sleep(random.uniform(0.4, 1.0))
                driver.get(product_url)
                _wait_for_product_ready(driver, timeout=25)
            self._ensure_hook(driver)
            wb._last_oos_refresh = time.time()
            ok = not self._page_looks_bad(driver)
            # OOS is not a hard page error — page can still be "ok" HTML with disabled CTA.
            if ok and self._shows_out_of_stock(driver):
                return False
            if ok:
                wb.ready = True
                wb.last_error = ""
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.warning("[farm] %s hard refresh failed: %s", wb.name, exc)
            return False
        finally:
            self._release_pdp_slot()

    def _shows_out_of_stock(self, driver: Any) -> bool:
        """True when UI shows SORRY/OUT OF STOCK or soft purchase-limit block."""
        # Soft quota / soft-error: PLACE PRE-ORDER can still look enabled in DOM.
        soft_markers = (
            "purchase limit has been reached",
            "the purchase limit has been reached",
            "we can't perform the requested operation",
            "cannot perform the requested operation",
        )
        hard_markers = (
            "sorry, out of stock",
            "sorry out of stock",
            "out of stock",
            "sold out",
            "currently unavailable",
            "pre-order closed",
            "preorder closed",
            "pre-orders closed",
            "msg.sorryoutofstock",
            "暫無存貨",
            "暂时缺货",
            "暫時缺貨",
            "售罄",
            "缺貨",
            "売り切れ",
            "在庫なし",
        )
        # Prefer live DOM (sidebar can sit past a huge KV image block in page_source).
        if self._dom_shows_unavailable(driver):
            return True
        try:
            # Do NOT truncate — KV thumbs alone often exceed 20KB before the CTA.
            low = (driver.page_source or "").lower()
        except Exception:  # noqa: BLE001
            return False
        if any(m in low for m in soft_markers):
            return True
        # Hard OOS only when there is no enabled PLACE PRE-ORDER CTA.
        if self._has_atc_button(driver):
            return False
        return any(m in low for m in hard_markers)

    def _dom_shows_unavailable(self, driver: Any) -> bool:
        """Detect OOS / soft-block from sidebar DOM (not truncated HTML dump)."""
        from selenium.webdriver.common.by import By

        try:
            # True OOS CTA: class is-noActive + SORRY OUT OF STOCK / data-bs-text-key.
            for btn in driver.find_elements(
                By.CSS_SELECTOR,
                "button.p-button, button.is-noActive, button[data-bs-text-key]",
            ):
                try:
                    if not btn.is_displayed():
                        continue
                    cls = (btn.get_attribute("class") or "").lower()
                    key = (btn.get_attribute("data-bs-text-key") or "").lower()
                    label = (btn.text or "").strip().lower()
                    if "msg.sorryoutofstock" in key or "sorryoutofstock" in key:
                        return True
                    if "is-noactive" in cls and (
                        "out of stock" in label or "sorry" in label
                    ):
                        return True
                    if "sorry, out of stock" in label or label == "sorry out of stock":
                        return True
                except Exception:  # noqa: BLE001
                    continue
            # Flag row on PDP sidebar.
            for flag in driver.find_elements(By.CSS_SELECTOR, ".p-flag__item, .o-items__sidebar-flag li"):
                try:
                    if not flag.is_displayed():
                        continue
                    text = (flag.text or "").strip().lower()
                    if text in ("out of stock", "sold out") or text.startswith("out of stock"):
                        return True
                except Exception:  # noqa: BLE001
                    continue
            # Soft purchase-limit copy near quantity / form.
            for el in driver.find_elements(
                By.CSS_SELECTOR,
                ".o-items__sidebar p, .o-items__sidebar span, .o-items__sidebar .p-lead, form p",
            ):
                try:
                    t = (el.text or "").strip().lower()
                    if not t:
                        continue
                    if "purchase limit has been reached" in t:
                        return True
                    if "can't perform the requested operation" in t:
                        return True
                    if "cannot perform the requested operation" in t:
                        return True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            return False
        return False

    def _has_atc_button(self, driver: Any) -> bool:
        from selenium.webdriver.common.by import By

        needles = (
            "PLACE PRE-ORDER",
            "ADD TO CART",
            "PLACE ORDER",
            "加入購物車",
            "預購",
            "立即預訂",
        )
        try:
            for btn in driver.find_elements(By.TAG_NAME, "button"):
                try:
                    if not btn.is_displayed() or not btn.is_enabled():
                        continue
                    label = (btn.text or btn.get_attribute("aria-label") or "").strip()
                    if not label:
                        continue
                    if any(n.lower() in label.lower() for n in needles):
                        # Explicitly reject OOS labels.
                        if "out of stock" in label.lower() or "sorry" in label.lower():
                            continue
                        return True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return False

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

    def _warm_home(self, driver: Any, *, home: str, name: str) -> None:
        """Browse /{area}/ briefly so the first PDP hit is not a cold direct open."""
        warm = max(0.0, float(getattr(self.config, "home_warmup_seconds", 0.0) or 0.0))
        if warm <= 0:
            return
        acquired = self._acquire_pdp_slot(name)
        try:
            print(f"[farm] {name} home warm-up ~{warm:.1f}s")
            logger.info("[farm] %s home warm-up seconds=%.1f", name, warm)
            driver.get(home)
            # Linger + light scroll so cookies/session look less bot-cold.
            end = time.time() + warm + random.uniform(0.2, 1.2)
            scrolled = False
            while time.time() < end and not self._stop.is_set():
                if not scrolled and time.time() + 0.6 < end:
                    try:
                        driver.execute_script(
                            "window.scrollBy({top: arguments[0], left: 0, behavior: 'smooth'});",
                            random.randint(120, 420),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    scrolled = True
                time.sleep(0.25)
            if scrolled:
                try:
                    driver.execute_script(
                        "window.scrollBy({top: arguments[0], left: 0, behavior: 'smooth'});",
                        -random.randint(40, 180),
                    )
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("[farm] %s home warm soft-fail: %s", name, exc)
        finally:
            if acquired:
                self._release_pdp_slot()

    def _open_stagger_delay(self, index: int) -> float:
        """Seconds to wait before Chrome launch / PDP for this instance (jittered)."""
        base = max(0.0, float(self.config.open_stagger_seconds))
        if base <= 0:
            return 0.0
        # Spread launches: index * base + random jitter so retries don't re-lockstep.
        return base * max(0, index) + random.uniform(0.0, max(0.2, base * 0.6))

    def _open_one(
        self,
        name: str,
        proxy: str,
        product_url: str,
        *,
        index: int = 0,
    ) -> FarmBrowser:
        wb = FarmBrowser(name=name, proxy=proxy)
        driver = None
        try:
            print(f"[farm] start {name} proxy={redact_proxy(proxy) or '-'}")
            # Stagger Chrome launch itself (not only PDP nav) — all 20 Chromes
            # starting at T0 was a major PAGE NOT AVAILABLE driver.
            launch_wait = self._open_stagger_delay(index)
            if launch_wait > 0:
                print(f"[farm] {name} wait {launch_wait:.1f}s before Chrome launch")
                end = time.time() + launch_wait
                while time.time() < end and not self._stop.is_set():
                    time.sleep(max(0.0, min(0.25, end - time.time())))
                if self._stop.is_set():
                    return wb

            # Headless hits PNA/WAF far more often — force headed for the farm.
            prev_bg = bool(self.config.background_mode)
            if prev_bg:
                logger.warning(
                    "[farm] %s BACKGROUND_MODE ignored for click farm (headed required)",
                    name,
                )
                self.config.background_mode = False
            try:
                identity = build_browser_identity(
                    index=index,
                    name=name,
                    config=self.config,
                    enable_profile=True,
                )
                print(
                    f"[farm] {name} identity size={identity.width}x{identity.height} "
                    f"tz={identity.timezone_id} lang={identity.accept_language.split(',')[0]} "
                    f"profile={'yes' if identity.profile_dir else 'no'}"
                )
                driver = _create_webdriver(
                    self.config,
                    proxy=proxy or "",
                    identity=identity,
                )
            finally:
                self.config.background_mode = prev_bg
            try:
                driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": _HOOK_JS},
                )
            except Exception:  # noqa: BLE001
                pass

            # Small extra jitter before home/PDP so origin hits stay spread.
            extra = random.uniform(0.15, 0.9)
            time.sleep(extra)

            home = f"{self.config.base_url}/{self.config.area_code}/"
            self._warm_home(driver, home=home, name=name)

            ok = self._load_pdp_with_retries(driver, product_url, home=home, name=name)
            self._ensure_hook(driver)
            wb.driver = driver
            wb.ready = ok
            if not wb.ready:
                wb.last_error = "PDP page not available after retries"
                print(
                    f"[farm] {name} PDP still unavailable after retries — "
                    "keeping browser; will heal before clicks"
                )
                # Keep driver alive on home so worker can heal later.
                try:
                    driver.get(home)
                except Exception:  # noqa: BLE001
                    pass
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

    def _load_pdp_with_retries(
        self,
        driver: Any,
        product_url: str,
        *,
        home: str,
        name: str,
        require_atc: bool = False,
    ) -> bool:
        retries = max(1, int(self.config.open_pdp_retries))
        base_wait = max(0.0, float(self.config.open_pdp_retry_wait))
        for attempt in range(1, retries + 1):
            if self._stop.is_set():
                return False
            if not self._acquire_pdp_slot(name):
                return False
            try:
                # Cache-bust on retries after the first.
                url = product_url if attempt == 1 else f"{product_url}?_={int(time.time() * 1000)}"
                driver.get(url)
                _wait_for_product_ready(driver, timeout=45)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[farm] %s PDP get attempt %s error: %s", name, attempt, exc)
            finally:
                self._release_pdp_slot()

            bad = self._page_looks_bad(driver)
            oos = self._shows_out_of_stock(driver) if require_atc else False
            if not bad and not oos:
                if require_atc and not self._has_atc_button(driver):
                    # Page loaded but still no PLACE PRE-ORDER — treat as soft fail.
                    oos = True
                else:
                    if attempt > 1:
                        print(f"[farm] {name} PDP OK on attempt {attempt}/{retries}")
                    return True

            reason = self._page_bad_reason(driver) if bad else (
                "OUT OF STOCK / no ATC button" if oos else "unknown"
            )
            print(f"[farm] {name} PDP bad attempt {attempt}/{retries}: {reason}")
            logger.warning("[farm] %s PDP bad attempt=%s/%s reason=%s", name, attempt, retries, reason)
            if attempt >= retries:
                break
            # Home bounce also gated so we don't stampede between retries.
            if self._acquire_pdp_slot(name):
                try:
                    driver.get(home)
                    time.sleep(random.uniform(0.6, 1.2))
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    self._release_pdp_slot()
            # Exponential-ish backoff + jitter so 20 instances don't retry in lockstep.
            wait = (base_wait * attempt) + random.uniform(0.5, 2.0)
            end = time.time() + wait
            while time.time() < end and not self._stop.is_set():
                time.sleep(max(0.0, min(0.2, end - time.time())))
        return False

    def _recover_pdp(self, wb: FarmBrowser, *, force_even_if_oos: bool = False) -> bool:
        driver = wb.driver
        if driver is None:
            return False
        product_url = self._product_url()
        home = f"{self.config.base_url}/{self.config.area_code}/"
        ok = self._load_pdp_with_retries(
            driver,
            product_url,
            home=home,
            name=wb.name,
            require_atc=force_even_if_oos,
        )
        if ok:
            self._ensure_hook(driver)
            wb.ready = True
            wb.last_error = ""
        return ok

    def _page_looks_bad(self, driver: Any) -> bool:
        return bool(self._page_bad_reason(driver))

    def _page_bad_reason(self, driver: Any) -> str:
        """Return short reason if PDP/error page looks unhealthy; else ''."""
        try:
            title = (driver.title or "").strip()
        except Exception:  # noqa: BLE001
            return "title unreadable"
        low_title = title.lower()
        if "page not available" in low_title:
            return f"title={title[:80]}"
        if any(x in low_title for x in ("500", "502", "503", "error", "unavailable")):
            return f"title={title[:80]}"
        try:
            source = (driver.page_source or "")[:12000].lower()
        except Exception:  # noqa: BLE001
            return "page_source unreadable"
        markers = (
            "page not available",
            "http error 500",
            "http error 502",
            "http error 503",
            "internal server error",
            "bad gateway",
            "service unavailable",
            "something went wrong",
        )
        for m in markers:
            if m in source:
                return m
        # Healthy PDP usually has cart CTA or qty control once SPA hydrates.
        healthy = (
            "place pre-order" in source
            or "add to cart" in source
            or "加入購物車" in source
            or "c-input-quantity" in source
            or "/item/" in (getattr(driver, "current_url", "") or "").lower()
        )
        # If still on item URL but no CTA yet, treat as soft-bad only when title empty
        # or body tiny (blank SPA shell after 500).
        if "/item/" in (getattr(driver, "current_url", "") or "").lower():
            if healthy:
                return ""
            if len(source) < 800:
                return "empty/short page body"
            # SPA mid-load — not necessarily bad; allow click attempt.
            return ""
        if not healthy and "p-bandai.com" in source:
            return "not on healthy PDP"
        return ""

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
