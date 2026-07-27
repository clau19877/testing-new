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

    def prepare(self) -> None:
        setup_logging(self.config.log_file, level=self.config.log_level)
        self.config.validate()
        logger.info(
            "prepare start area=%s direct=%s search=%s cart=%s sessions_file=%s proxy=%s",
            self.config.area_code,
            self.config.product_codes,
            self.config.search_keywords,
            self.config.enable_add_to_cart,
            self.config.sessions_file,
            redact_proxy(self.config.proxy_url) or "-",
        )

        # CSV tasks: N rows => N parallel sessions, each with a random proxy.
        from .task_runner import ensure_tasks_ready, task_csv_exists

        if task_csv_exists(self.config):
            results = ensure_tasks_ready(self.config)
            failed = [r for r in results if not r.ok]
            if failed and self.config.enable_add_to_cart:
                names = ", ".join(r.name for r in failed)
                raise RuntimeError(
                    f"CSV task login failed for: {names}. "
                    "Fix credentials/proxies and retry, or set ENABLE_ADD_TO_CART=0."
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
                runtime.client.bootstrap()
                logger.info(
                    "bootstrapped session=%s proxy=%s",
                    runtime.client.name,
                    redact_proxy(runtime.client.proxy) or "-",
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"bootstrap failed for session={runtime.client.name}: {exc}"
                log_exception(logger, msg, exc)
                raise RuntimeError(msg) from exc

        if self.config.enable_add_to_cart:
            self._ensure_logged_in_sessions()

        print(
            f"Active sessions: {len(self.sessions)} | cart_mode={self.config.cart_mode}"
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
                    client.refresh_csrf()
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

    def run_once(self) -> RunReport:
        report = RunReport()
        allowed = {s.lower() for s in self.config.sale_statuses}
        assert self.client is not None

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

        for runtime in sessions:
            client = runtime.client
            ok, note = self._add_with_client(client, product, detail=detail)
            if ok:
                successes.append(client.name)
                result.added_to_cart = True
                result.session_name = client.name
                result.detail_note = note
                logger.info("[cart] session=%s %s", client.name, note)
                if self.config.cart_mode == "first" or self.config.cart_mode == "round_robin":
                    self.seen_codes.add(product.product_code)
                    return
            else:
                failures.append(f"{client.name}:{note}")
                logger.warning("[cart] session=%s failed: %s", client.name, note)

        if successes:
            result.added_to_cart = True
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

    def _add_with_client(
        self,
        client: PBandaiHkClient,
        product: ProductHit,
        detail: Optional[dict] = None,
    ) -> tuple[bool, str]:
        retries = self.config.add_cart_retry_count
        last_note = "unknown"
        for attempt in range(1, retries + 1):
            try:
                product_detail = detail or client.get_product(product.product_code)
                purchase_available = bool(product_detail.get("purchaseAvailable"))
                area_item_no = client.pick_area_item_no(product_detail)
                if not purchase_available:
                    return False, "purchaseAvailable=false"
                if not area_item_no:
                    return False, "no areaItemNo"
                client.add_to_cart(area_item_no, qty=self.config.cart_qty)
                return True, f"added via {area_item_no}"
            except Exception as exc:  # noqa: BLE001
                last_note = f"attempt {attempt}/{retries} failed: {exc}"
                log_exception(
                    logger,
                    f"add-to-cart failed session={client.name} product={product.product_code} "
                    f"(attempt {attempt}/{retries})",
                    exc,
                )
                time.sleep(1)
        return False, last_note

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
                logger.info("[loop] sleep %ss", self.config.retry_wait)
                time.sleep(self.config.retry_wait)
