from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Set

import schedule

from .api import PBandaiHkClient, ProductHit
from .config import Config
from .logging_utils import get_logger, log_exception, setup_logging
from .notify import maybe_send_email
from .session_login import login_and_transfer_cookies

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
        self.client = client or PBandaiHkClient(
            base_url=config.base_url,
            area_code=config.area_code,
            accept_language=config.accept_language,
        )
        self.remaining_targets: List[str] = list(config.target_list)
        self.remaining_direct_codes: List[str] = list(config.product_codes)
        self.seen_codes: Set[str] = set()

    def prepare(self) -> None:
        setup_logging(self.config.log_file, level=self.config.log_level)
        self.config.validate()
        logger.info(
            "prepare start area=%s direct=%s search=%s cart=%s",
            self.config.area_code,
            self.config.product_codes,
            self.config.search_keywords,
            self.config.enable_add_to_cart,
        )
        try:
            self.client.bootstrap()
        except Exception as exc:  # noqa: BLE001
            log_exception(logger, "bootstrap failed", exc)
            raise
        if self.config.enable_add_to_cart:
            try:
                login_and_transfer_cookies(self.config, self.client)
            except Exception as exc:  # noqa: BLE001
                log_exception(logger, "login/cookie transfer failed", exc)
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
                    self.remaining_targets.remove(fine)
            else:
                result.detail_note = "monitor-only"
                self.seen_codes.add(hit.product_code)

            report.matches.append(result)

    def _try_add_to_cart(
        self,
        result: MatchResult,
        report: RunReport,
        detail: Optional[dict] = None,
    ) -> None:
        product = result.product
        retries = self.config.add_cart_retry_count
        for attempt in range(1, retries + 1):
            try:
                product_detail = detail or self.client.get_product(product.product_code)
                result.purchase_available = bool(product_detail.get("purchaseAvailable"))
                area_item_no = self.client.pick_area_item_no(product_detail)
                result.area_item_no = area_item_no
                if not result.purchase_available:
                    result.detail_note = "purchaseAvailable=false"
                    logger.info("[cart] not purchasable yet: %s", product.product_code)
                    return
                if not area_item_no:
                    result.detail_note = "no areaItemNo"
                    logger.warning("[cart] missing areaItemNo for %s", product.product_code)
                    return
                self.client.add_to_cart(area_item_no, qty=self.config.cart_qty)
                result.added_to_cart = True
                result.detail_note = f"added via {area_item_no}"
                self.seen_codes.add(product.product_code)
                logger.info("[cart] added %s via %s", product.product_code, area_item_no)
                return
            except Exception as exc:  # noqa: BLE001
                result.detail_note = f"attempt {attempt}/{retries} failed: {exc}"
                log_exception(
                    logger,
                    f"add-to-cart failed for {product.product_code} "
                    f"(attempt {attempt}/{retries})",
                    exc,
                )
                time.sleep(1)
        report.errors.append(
            f"failed to add {product.product_code}: {result.detail_note}"
        )

    def _notify(self, report: RunReport) -> None:
        if not report.matches and not report.errors:
            subject = "P-Bandai HK: no matches"
            body = (
                "P-Bandai HK notify\n"
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
                    f"  code={p.product_code} source={match.source} kw={match.matched_keyword}\n"
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
