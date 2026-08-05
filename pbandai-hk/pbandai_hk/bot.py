from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Set

import schedule

from .api import PBandaiHkClient, ProductHit
from .config import Config
from .logging_utils import get_logger, log_exception, setup_logging
from .notify import maybe_send_email
from .proxy_util import redact_proxy
from .session_login import login_and_transfer_cookies
from .sessions import RuntimeSession, SessionSpec, build_runtime_sessions

logger = get_logger("bot")


@dataclass
class MatchResult:
    product: ProductHit
    matched_keyword: str
    purchase_available: Optional[bool] = None
    area_item_no: Optional[str] = None
    added_to_cart: bool = False
    detail_note: str = ""
    source: str = "search"  # search | direct
    session_name: str = ""
    all_sessions_ok: bool = False


@dataclass
class RunReport:
    matches: List[MatchResult] = field(default_factory=list)
    scanned: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def added_count(self) -> int:
        return sum(1 for m in self.matches if m.added_to_cart)


class PBandaiHkBot:
    def __init__(self, config: Config, client: Optional[PBandaiHkClient] = None) -> None:
        self.config = config
        self.sessions: List[RuntimeSession] = []
        # Keep a primary client for monitor/search (first session / default).
        self.client = client
        self.remaining_targets: List[str] = list(config.target_list)
        self.remaining_direct_codes: List[str] = list(config.product_codes)
        self.seen_codes: Set[str] = set()
        self._rr_index = 0
        self.warm_pool = None  # WarmBrowserPool | None
        self.click_farm = None  # ClickFarm | None
        self._next_sleep_hint: Optional[float] = None

    def prepare(self) -> None:
        setup_logging(self.config.log_file, level=self.config.log_level)
        self.config.validate()
        from . import BUILD_ID, __version__

        logger.info(
            "prepare start version=%s build=%s area=%s direct=%s search=%s cart=%s "
            "click_farm=%s instances=%s click_at_second=%s discord=%s stop_on_first=%s",
            __version__,
            BUILD_ID,
            self.config.area_code,
            self.config.product_codes,
            self.config.search_keywords,
            self.config.enable_add_to_cart,
            self.config.click_farm,
            self.config.browser_instances,
            self.config.click_at_second,
            "yes" if self.config.discord_webhook_url else "no",
            self.config.stop_on_first_cart,
        )
        print(f"P-Bandai HK bot version={__version__} build={BUILD_ID}")

        # Guest click farm: no login / no task.csv accounts.
        if self.config.enable_add_to_cart and self.config.click_farm:
            codes = list(self.config.product_codes)
            if not codes:
                raise ValueError(
                    "CLICK_FARM=1 requires PRODUCT_LINKS or PRODUCT_CODES "
                    "(browsers park on the product page)"
                )
            from .click_farm import ClickFarm

            self.click_farm = ClickFarm(config=self.config, product_code=codes[0])
            planned = self.click_farm.prepare()
            if planned <= 0:
                raise RuntimeError("click farm: no instances planned")
            at = int(self.config.click_at_second)
            sched = f":{at:02d}/min" if at >= 0 else f"every {self.config.click_interval_seconds}s"
            discord_state = "on" if self.config.discord_webhook_url else "OFF"
            print(
                f"Click farm planned={planned} independent instance(s) "
                f"| schedule={sched} (keep going) "
                f"| discord={discord_state}"
            )
            if not self.config.discord_webhook_url:
                print(
                    "WARNING: DISCORD_WEBHOOK_URL is empty — Discord will not be notified.\n"
                    "         Put your webhook in .env OR in discord_webhook.txt next to the bot."
                )
            return

        # CSV tasks: N rows => N parallel sessions, each with a random proxy.
        from .task_runner import ensure_tasks_ready, task_csv_exists

        if task_csv_exists(self.config):
            results = ensure_tasks_ready(self.config)
            failed = [r for r in results if not r.ok]
            if failed and self.config.enable_add_to_cart:
                # Allow continue when at least one session logged in.
                ok = [r for r in results if r.ok]
                names = ", ".join(r.name for r in failed)
                if not ok:
                    raise RuntimeError(
                        f"CSV task login failed for: {names}. "
                        "Often caused by proxy WAF on /api/context/member. "
                        "Or set CLICK_FARM=1 for guest click mode (no login)."
                    )
                logger.warning(
                    "CSV login partial failure; continuing with %s ok, failed=%s",
                    len(ok),
                    names,
                )
                print(
                    f"WARNING: some CSV logins failed ({names}); "
                    f"continuing with {len(ok)} session(s)."
                )

        if self.client is None:
            self.sessions = build_runtime_sessions(
                sessions_file=self.config.sessions_file,
                base_url=self.config.base_url,
                area_code=self.config.area_code,
                accept_language=self.config.accept_language,
                fallback_proxy=self.config.proxy_url,
                cookie_file=self.config.cookie_file,
            )
            self.client = self.sessions[0].client
        elif not self.sessions:
            self.sessions = [
                RuntimeSession(
                    spec=SessionSpec(
                        name=self.client.name,
                        proxy=self.client.proxy,
                    ),
                    client=self.client,
                )
            ]

        for runtime in self.sessions:
            try:
                runtime.client.bootstrap(required=False)
                # Still try once hard if we have no csrf yet
                if not runtime.client.csrf_token:
                    try:
                        runtime.client.refresh_csrf(required=False)
                    except Exception:  # noqa: BLE001
                        pass
                logger.info(
                    "bootstrapped session=%s proxy=%s csrf=%s",
                    runtime.client.name,
                    redact_proxy(runtime.client.proxy) or "-",
                    "yes" if runtime.client.csrf_token else "no",
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"bootstrap failed for session={runtime.client.name}: {exc}"
                log_exception(logger, msg, exc)
                # Don't abort prepare — warm/browser cart can still work with cookies.
                print(f"WARNING: {msg}")

        if self.config.enable_add_to_cart:
            # Legacy logged-in path only when click farm is off.
            self._ensure_logged_in_sessions()
            self._maybe_prepare_warm_pool()

        print(
            f"Active sessions: {len(self.sessions)} | cart_mode={self.config.cart_mode}"
            f" | cart_method={self.config.cart_method}"
            f" | prewarm={int(self.config.prewarm_browsers or self.config.cart_method == 'warm')}"
        )
        for runtime in self.sessions:
            print(
                f"  - {runtime.client.name} proxy={redact_proxy(runtime.client.proxy) or '-'}"
            )

    def _ensure_logged_in_sessions(self) -> None:
        for runtime in self.sessions:
            client = runtime.client
            has_session_cookie = any(
                c.name.upper() == "SESSION" for c in client.session.cookies
            )
            if has_session_cookie and not self.config.force_browser_login:
                try:
                    client.refresh_csrf(required=False)
                    summary = client.cart_summary()
                    logger.info(
                        "session=%s already authenticated summary=%s",
                        client.name,
                        summary,
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "session=%s cookie refresh failed, will re-login: %s",
                        client.name,
                        exc,
                    )

            cookie_path = getattr(runtime.spec, "cookie_file", "") or ""
            if not cookie_path:
                cookie_path = f"sessions/{client.name}.cookies.json"
                runtime.spec.cookie_file = cookie_path

            print(f"\nLogin required for session: {client.name}")
            try:
                login_and_transfer_cookies(
                    self.config,
                    client,
                    proxy=client.proxy or self.config.proxy_url,
                    save_cookie_file=cookie_path,
                    force_browser=True,
                )
                # Persist session entry
                from .sessions import upsert_session_spec

                upsert_session_spec(
                    self.config.sessions_file,
                    SessionSpec(
                        name=client.name,
                        enabled=True,
                        proxy=client.proxy or self.config.proxy_url,
                        cookie_file=cookie_path,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                log_exception(logger, f"login failed for session={client.name}", exc)
                print(
                    "\nBrowser login failed.\n"
                    "Quick fixes:\n"
                    "  1) Install/update Google Chrome or Microsoft Edge\n"
                    "  2) Delete driver cache folder: %USERPROFILE%\\.wdm\n"
                    "  3) Set BROWSER=edge in .env and retry\n"
                    "  4) Or set ENABLE_ADD_TO_CART=0 for monitor-only mode\n"
                )
                raise

    def _want_warm(self) -> bool:
        if not self.config.enable_add_to_cart:
            return False
        if self.config.cart_method == "warm":
            return True
        if self.config.cart_method in {"auto", "browser"} and self.config.prewarm_browsers:
            return True
        return False

    def _maybe_prepare_warm_pool(self) -> None:
        if not self._want_warm():
            return
        codes = list(self.remaining_direct_codes) or list(self.config.product_codes)
        if not codes:
            logger.warning(
                "[warm] PREWARM/CART_METHOD=warm set but no PRODUCT_LINKS/CODES; "
                "warm pool skipped (keyword-only mode cannot pre-park)"
            )
            print(
                "[warm] skipped: set PRODUCT_LINKS/PRODUCT_CODES so browsers can park "
                "on the product page before the drop"
            )
            return
        product_code = codes[0]
        from .warm_cart import WarmBrowserPool

        if self.warm_pool is not None:
            try:
                self.warm_pool.close()
            except Exception:  # noqa: BLE001
                pass
        self.warm_pool = WarmBrowserPool(config=self.config)
        ready = self.warm_pool.prepare(self.sessions, product_code)
        if ready <= 0:
            logger.warning("[warm] no browsers ready — will fall back to cold browser/API")
            print("[warm] WARNING: no warm browsers ready; cold fallback will be used")

    def run_once(self) -> RunReport:
        report = RunReport()
        allowed = {s.lower() for s in self.config.sale_statuses}
        assert self.client is not None
        self._next_sleep_hint = None

        # 1) Direct product links / codes
        for code in list(self.remaining_direct_codes):
            try:
                hit, detail = self.client.resolve_direct_product(code)
                report.scanned += 1
                logger.info(
                    "[direct] %s status=%s purchaseAvailable=%s name=%s",
                    code,
                    hit.sale_status,
                    detail.get("purchaseAvailable"),
                    hit.display_name,
                )
                if allowed and hit.sale_status.lower() not in allowed:
                    logger.info(
                        "[direct] skip %s due to sale_status=%s",
                        code,
                        hit.sale_status,
                    )
                    continue
                if code in self.seen_codes and not self.config.enable_add_to_cart:
                    continue

                result = MatchResult(
                    product=hit,
                    matched_keyword=code,
                    purchase_available=bool(detail.get("purchaseAvailable")),
                    source="direct",
                )
                if self.config.enable_add_to_cart:
                    from .diagnostics import (
                        is_cart_eligible,
                        log_cart_diagnosis,
                        seconds_until_order_start,
                    )

                    picked = self.client.pick_area_item_no(detail) or ""
                    signals = is_cart_eligible(
                        detail,
                        product_code=code,
                        sale_status=hit.sale_status,
                        picked_area_item_no=picked,
                    )
                    log_cart_diagnosis(
                        signals,
                        session_name=self.client.name,
                        stage="direct-scan",
                    )
                    until = seconds_until_order_start(detail)
                    if until is not None and until > 0:
                        lead = max(0, int(self.config.drop_lead_seconds))
                        hint = max(1.0, until - lead)
                        if self._next_sleep_hint is None or hint < self._next_sleep_hint:
                            self._next_sleep_hint = hint
                    if not signals.cart_eligible:
                        result.detail_note = (
                            "waiting: " + "; ".join(signals.blocking_reasons)
                            or "not cart-eligible"
                        )
                        logger.info(
                            "[wait] %s not cart-eligible yet (%s)",
                            code,
                            result.detail_note,
                        )
                    else:
                        # Note: top-level purchaseAvailable can be false while the
                        # site still allows add-to-cart (inventory/availability).
                        self._try_add_to_cart(result, report, detail=detail)
                        if result.added_to_cart and code in self.remaining_direct_codes:
                            if self.config.cart_mode != "all" or result.all_sessions_ok:
                                self.remaining_direct_codes.remove(code)
                else:
                    result.detail_note = (
                        f"monitor-only purchaseAvailable={detail.get('purchaseAvailable')}"
                    )
                    self.seen_codes.add(code)
                report.matches.append(result)
            except Exception as exc:  # noqa: BLE001
                msg = f"direct link failed for '{code}': {exc}"
                report.errors.append(msg)
                log_exception(logger, msg, exc)

        # 2) Keyword search (optional)
        if self.config.search_keywords:
            self._scan_search(report)

        self._notify(report)
        if report.errors:
            logger.error("run finished with %s error(s)", len(report.errors))
        else:
            logger.info(
                "run finished scanned=%s matches=%s added=%s",
                report.scanned,
                len(report.matches),
                report.added_count,
            )
        return report

    def _scan_search(self, report: RunReport) -> None:
        assert self.client is not None
        precheck = self.config.precheck_list or self.remaining_targets
        targets = self.remaining_targets
        candidates: List[ProductHit] = []

        for keyword in self.config.search_keywords:
            try:
                page_hits = self.client.iter_search_products(
                    keyword,
                    limit=self.config.search_limit,
                    max_pages=self.config.search_max_pages,
                    product_statuses=self.config.sale_statuses,
                )
                report.scanned += len(page_hits)
                candidates.extend(page_hits)
                logger.info("[search] '%s' -> %s hits", keyword, len(page_hits))
            except Exception as exc:  # noqa: BLE001
                msg = f"search failed for '{keyword}': {exc}"
                report.errors.append(msg)
                log_exception(logger, msg, exc)

        unique: List[ProductHit] = []
        seen: Set[str] = set()
        for hit in candidates:
            if hit.product_code in seen:
                continue
            seen.add(hit.product_code)
            unique.append(hit)

        for hit in unique:
            coarse = hit.matches(precheck)
            if not coarse:
                continue
            fine = hit.matches(targets) if targets else coarse
            if not fine:
                continue
            if hit.product_code in self.seen_codes and not self.config.enable_add_to_cart:
                continue

            result = MatchResult(product=hit, matched_keyword=fine, source="search")
            logger.info(
                "[match] %s %s %s (kw=%s)",
                hit.sale_status,
                hit.product_code,
                hit.display_name,
                fine,
            )

            if self.config.enable_add_to_cart:
                self._try_add_to_cart(result, report)
                if result.added_to_cart and fine in self.remaining_targets:
                    if self.config.cart_mode != "all" or result.all_sessions_ok:
                        self.remaining_targets.remove(fine)
            else:
                result.detail_note = "monitor-only"
                self.seen_codes.add(hit.product_code)

            report.matches.append(result)

    def _ordered_sessions(self) -> List[RuntimeSession]:
        if not self.sessions:
            return []
        if self.config.cart_mode != "round_robin":
            return list(self.sessions)
        n = len(self.sessions)
        start = self._rr_index % n
        self._rr_index += 1
        return self.sessions[start:] + self.sessions[:start]

    def _try_add_to_cart(
        self,
        result: MatchResult,
        report: RunReport,
        detail: Optional[dict] = None,
    ) -> None:
        product = result.product
        sessions = self._ordered_sessions()
        successes: List[str] = []
        failures: List[str] = []

        # Low-stock drops (e.g. A2891018001 availableQty=2): fire all sessions together.
        use_parallel = (
            self.config.cart_mode == "all"
            and self.config.cart_parallel
            and len(sessions) > 1
        )
        if use_parallel:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _staggered_add(runtime: RuntimeSession, delay: float) -> tuple[bool, str]:
                # Stagger starts so 3× simultaneous fetch don't trip WAF together.
                if delay > 0:
                    time.sleep(delay)
                return self._add_with_client(runtime.client, product, detail)

            print(f"[cart] parallel add across {len(sessions)} sessions (staggered)")
            logger.info("[cart] parallel add sessions=%s staggered", len(sessions))
            with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
                futures = {
                    pool.submit(_staggered_add, runtime, idx * 0.25): runtime
                    for idx, runtime in enumerate(sessions)
                }
                for fut in as_completed(futures):
                    runtime = futures[fut]
                    try:
                        ok, note = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        ok, note = False, str(exc)
                    if ok:
                        successes.append(runtime.client.name)
                        logger.info("[cart] session=%s %s", runtime.client.name, note)
                    else:
                        failures.append(f"{runtime.client.name}:{note}")
                        logger.warning(
                            "[cart] session=%s failed: %s", runtime.client.name, note
                        )
        else:
            for runtime in sessions:
                client = runtime.client
                ok, note = self._add_with_client(client, product, detail=detail)
                if ok:
                    successes.append(client.name)
                    result.added_to_cart = True
                    result.session_name = client.name
                    result.detail_note = note
                    logger.info("[cart] session=%s %s", client.name, note)
                    if self.config.cart_mode in {"first", "round_robin"}:
                        self.seen_codes.add(product.product_code)
                        return
                else:
                    failures.append(f"{client.name}:{note}")
                    logger.warning("[cart] session=%s failed: %s", client.name, note)

        if successes:
            result.added_to_cart = True
            result.session_name = successes[0]
            result.all_sessions_ok = len(successes) >= len(sessions) and not failures
            result.detail_note = (
                f"sessions_ok={','.join(successes)}; failed={','.join(failures) or '-'}"
            )
            if self.config.cart_mode != "all" or result.all_sessions_ok:
                self.seen_codes.add(product.product_code)
            return

        result.detail_note = "; ".join(failures) or "no sessions available"
        report.errors.append(
            f"failed to add {product.product_code}: {result.detail_note}"
        )
        # Keep pressure on gateway failures; stock/preallocation needs slower retries.
        if any(_is_stock_or_business_cart_error(f) for f in failures):
            self._next_sleep_hint = 1.5
            logger.info(
                "[cart] stock/preallocation failures — next loop sleep ~1.5s"
            )
        elif any(_is_retryable_cart_failure(f) for f in failures):
            self._next_sleep_hint = 0.25
            logger.info(
                "[cart] retryable failures — next loop sleep ~0.25s (keep trying)"
            )

    def _add_with_client(
        self,
        client: PBandaiHkClient,
        product: ProductHit,
        detail: Optional[dict] = None,
    ) -> tuple[bool, str]:
        from .diagnostics import is_cart_eligible, log_cart_diagnosis

        retries = max(1, int(self.config.add_cart_retry_count))
        last_note = "unknown"
        method = self.config.cart_method
        for attempt in range(1, retries + 1):
            try:
                # Refresh product after first attempt so sold-out / status updates apply.
                product_detail = (
                    detail if (attempt == 1 and detail is not None) else client.get_product(product.product_code)
                )
                if attempt > 1:
                    detail = None
                area_item_no = client.pick_area_item_no(product_detail)
                signals = is_cart_eligible(
                    product_detail,
                    product_code=product.product_code,
                    sale_status=product.sale_status,
                    picked_area_item_no=area_item_no or "",
                )
                if attempt == 1 or not signals.cart_eligible:
                    log_cart_diagnosis(
                        signals,
                        session_name=client.name,
                        stage=f"add-attempt-{attempt}",
                    )
                if not signals.cart_eligible:
                    return False, "not-eligible: " + "; ".join(signals.blocking_reasons)
                if not area_item_no:
                    return False, "no areaItemNo"

                qty = int(self.config.cart_qty)
                if signals.max_qty is not None and signals.max_qty > 0:
                    qty = max(1, min(qty, int(signals.max_qty)))

                if method == "browser":
                    ok, note = self._finalize_cart_result(
                        self._browser_add(client, product, area_item_no, qty=qty)
                    )
                    if ok:
                        return True, note
                    last_note = note
                    if attempt < retries and _is_retryable_cart_failure(note):
                        time.sleep(0.2)
                        continue
                    return False, note

                if method == "warm":
                    ok, note = self._finalize_cart_result(
                        self._warm_add(client, product, area_item_no, qty=qty)
                    )
                    if ok:
                        return True, note
                    last_note = note
                    logger.warning(
                        "[cart] session=%s warm attempt %s/%s failed: %s",
                        client.name,
                        attempt,
                        retries,
                        note[:180],
                    )
                    # 409/preallocation = Bandai OOS/hold — do not burn all 8 bursts.
                    if _is_stock_or_business_cart_error(note):
                        if attempt >= min(3, retries):
                            return False, note
                        time.sleep(0.6)
                        continue
                    if attempt < retries and _is_retryable_cart_failure(note):
                        try:
                            client.refresh_csrf(required=False)
                        except Exception:  # noqa: BLE001
                            pass
                        time.sleep(0.15)
                        continue
                    # Last ditch: API once (often WAF 501, but cheap).
                    if attempt >= retries:
                        try:
                            client.add_to_cart(
                                area_item_no,
                                qty=qty,
                                product_code=product.product_code,
                            )
                            return True, f"api-added via {area_item_no} qty={qty} (after warm)"
                        except Exception as api_exc:  # noqa: BLE001
                            return False, f"{last_note}; api={api_exc}"
                    continue

                # auto: prefer warm pool (no HTML reload) when ready
                if self.warm_pool is not None and self.warm_pool.ready_count > 0:
                    ok, note = self._finalize_cart_result(
                        self._warm_add(client, product, area_item_no, qty=qty)
                    )
                    if ok:
                        return True, note
                    last_note = note
                    logger.warning(
                        "[cart] session=%s warm failed (%s); trying API",
                        client.name,
                        note[:160],
                    )

                logger.info(
                    "[cart] session=%s attempting API add areaItemNo=%s "
                    "purchaseAvailable=%s availableQty=%s qty=%s",
                    client.name,
                    area_item_no,
                    signals.purchase_available,
                    signals.available_qty,
                    qty,
                )
                try:
                    client.add_to_cart(
                        area_item_no,
                        qty=qty,
                        product_code=product.product_code,
                    )
                    return True, f"api-added via {area_item_no} qty={qty}"
                except Exception as api_exc:  # noqa: BLE001
                    msg = str(api_exc)
                    last_note = msg
                    waf_blocked = (
                        "501" in msg
                        or "503" in msg
                        or "502" in msg
                        or "WAF" in msg
                        or "HTML" in msg
                        or "Page not available" in msg
                    )
                    if method == "auto" and waf_blocked:
                        logger.warning(
                            "[cart] session=%s API blocked (%s); falling back to browser/warm",
                            client.name,
                            msg[:160],
                        )
                        if self.warm_pool is not None and self.warm_pool.ready_count > 0:
                            ok, note = self._finalize_cart_result(
                                self._warm_add(client, product, area_item_no, qty=qty)
                            )
                            if ok:
                                return True, note
                            last_note = note
                        ok, note = self._finalize_cart_result(
                            self._browser_add(client, product, area_item_no, qty=qty)
                        )
                        if ok:
                            return True, note
                        last_note = note
                        if attempt < retries and _is_retryable_cart_failure(last_note):
                            time.sleep(0.2)
                            continue
                        raise
                    if attempt < retries and _is_retryable_cart_failure(msg):
                        time.sleep(0.2)
                        continue
                    raise
            except Exception as exc:  # noqa: BLE001
                last_note = f"attempt {attempt}/{retries} failed: {exc}"
                log_exception(
                    logger,
                    f"add-to-cart failed session={client.name} product={product.product_code} "
                    f"(attempt {attempt}/{retries})",
                    exc,
                )
                detail = None
                time.sleep(0.3 if attempt < retries else 0)
        return False, last_note

    def _finalize_cart_result(self, result: tuple[bool, str]) -> tuple[bool, str]:
        ok, note = result
        if not ok:
            return ok, note
        if not self.config.require_cart_increase:
            return ok, note
        soft = "verify cart on site" in (note or "").lower()
        if soft:
            logger.warning(
                "[cart] rejecting soft success (no cart-count increase): %s",
                note,
            )
            return False, f"unverified: {note}"
        return ok, note

    def _warm_add(
        self,
        client: PBandaiHkClient,
        product: ProductHit,
        area_item_no: str,
        qty: Optional[int] = None,
    ) -> tuple[bool, str]:
        if self.warm_pool is None or self.warm_pool.ready_count <= 0:
            return False, "warm pool not ready"
        try:
            self.warm_pool.ensure_product(product.product_code)
        except Exception as exc:  # noqa: BLE001
            log_exception(logger, "warm ensure_product failed", exc)
        return self.warm_pool.add_to_cart(
            client,
            product_code=product.product_code,
            area_item_no=area_item_no,
            qty=int(qty if qty is not None else self.config.cart_qty),
        )

    def _browser_add(
        self,
        client: PBandaiHkClient,
        product: ProductHit,
        area_item_no: str,
        qty: Optional[int] = None,
    ) -> tuple[bool, str]:
        from .browser_cart import browser_add_to_cart

        cookie_file = ""
        for runtime in self.sessions:
            if runtime.client is client or runtime.client.name == client.name:
                cookie_file = getattr(runtime.spec, "cookie_file", "") or ""
                break
        if not cookie_file:
            cookie_file = f"sessions/{client.name}.cookies.json"
        return browser_add_to_cart(
            self.config,
            client,
            product_code=product.product_code,
            area_item_no=area_item_no,
            qty=int(qty if qty is not None else self.config.cart_qty),
            proxy=client.proxy or self.config.proxy_url,
            cookie_file=cookie_file,
        )

    def _notify(self, report: RunReport) -> None:
        session_names = ",".join(s.client.name for s in self.sessions) or "-"
        if not report.matches and not report.errors:
            subject = "P-Bandai HK: no matches"
            body = (
                "P-Bandai HK notify\n"
                f"sessions={session_names}\n"
                f"direct={','.join(self.remaining_direct_codes)}\n"
                f"targets={','.join(self.remaining_targets)}\n"
                f"scanned={report.scanned}\n"
                "No matching products this run.\n"
            )
        else:
            if report.added_count:
                subject = f"P-Bandai HK: added {report.added_count} item(s)"
            elif report.matches:
                subject = f"P-Bandai HK: {len(report.matches)} match(es)"
            else:
                subject = "P-Bandai HK: errors"
            lines = [
                "P-Bandai HK notify",
                f"sessions={session_names}",
                f"direct={','.join(self.remaining_direct_codes)}",
                f"targets={','.join(self.remaining_targets)}",
                f"scanned={report.scanned}",
                "",
            ]
            for match in report.matches:
                p = match.product
                price = ""
                if p.price_amount is not None:
                    price = f" | {p.price_amount} {p.currency or ''}".rstrip()
                lines.append(
                    f"- [{p.sale_status}] {p.display_name}{price}\n"
                    f"  code={p.product_code} source={match.source} "
                    f"session={match.session_name or '-'} kw={match.matched_keyword}\n"
                    f"  {p.url}\n"
                    f"  note={match.detail_note}"
                )
            if report.errors:
                lines.append("")
                lines.append("Errors:")
                lines.extend(f"- {err}" for err in report.errors)
            body = "\n".join(lines) + "\n"

        try:
            status = maybe_send_email(self.config, subject, body)
            logger.info("[email] %s", status)
        except Exception as exc:  # noqa: BLE001
            log_exception(logger, "email notify failed", exc)
            report.errors.append(f"email notify failed: {exc}")

    def run_forever(self) -> None:
        self.prepare()
        try:
            if self.click_farm is not None:
                logger.info("[farm] starting click loop")
                successes = self.click_farm.run()
                print(
                    f"[farm] finished carts={len(successes)} "
                    f"instances={[s.name for s in successes]}"
                )
                if successes:
                    print(
                        "[farm] log in to those Chrome windows and complete "
                        "checkout manually (cookies saved under logs/)"
                    )
                return

            if self.config.schedule_mode:
                if not self.config.execute_times:
                    raise ValueError("SCHEDULE_MODE=1 requires EXECUTE_TIME")
                for when in self.config.execute_times:
                    schedule.every().day.at(when).do(self.run_once)
                    logger.info("[schedule] registered %s", when)
                logger.info("[schedule] waiting...")
                while True:
                    schedule.run_pending()
                    time.sleep(self.config.schedule_polling_period)
            else:
                logger.info("[loop] immediate mode")
                while True:
                    self.run_once()
                    if self.config.enable_add_to_cart and not (
                        self.remaining_targets or self.remaining_direct_codes
                    ):
                        logger.info("[loop] all targets handled; exiting")
                        break
                    sleep_for = float(self.config.retry_wait)
                    if self._next_sleep_hint is not None:
                        hint = float(self._next_sleep_hint)
                        lead = max(5, int(self.config.drop_lead_seconds))
                        if hint > lead:
                            # Far from orderStartDate: sleep until ~T-lead (no busy poll).
                            sleep_for = hint
                            logger.info(
                                "[loop] order opens in ~%.0fs; sleeping until ~T-%ss",
                                hint + lead,
                                lead,
                            )
                        else:
                            # Near drop / retryable 503 pressure — poll hard.
                            sleep_for = min(sleep_for, 0.25)
                            if hint <= 1.0:
                                logger.info("[loop] cart pressure / near drop — fast poll")
                            else:
                                logger.info("[loop] near drop — fast poll")
                    sleep_for = max(0.2, sleep_for)
                    logger.info("[loop] sleep %.1fs", sleep_for)
                    time.sleep(sleep_for)
        finally:
            if self.click_farm is not None:
                try:
                    self.click_farm.close()
                except Exception:  # noqa: BLE001
                    pass
                self.click_farm = None
            if self.warm_pool is not None:
                try:
                    self.warm_pool.close()
                except Exception:  # noqa: BLE001
                    pass
                self.warm_pool = None


def _is_retryable_cart_failure(note: str) -> bool:
    from .browser_cart import _is_retryable_cart_note, _is_stock_or_business_cart_error

    if _is_stock_or_business_cart_error(note):
        return False
    if _is_retryable_cart_note(note):
        return True
    text = (note or "").lower()
    # Narrow extras — do NOT match bare "warm failed" (that includes 409 stock).
    needles = (
        "click=no button",
        "no button",
        "waf",
        "501",
        "html error",
    )
    return any(n in text for n in needles)


def _is_stock_or_business_cart_error(note: str) -> bool:
    from .browser_cart import _is_stock_or_business_cart_error as _impl

    return _impl(note)
