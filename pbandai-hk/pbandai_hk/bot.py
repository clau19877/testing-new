from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Set

import schedule

from .api import PBandaiHkClient, ProductHit
from .config import Config
from .notify import maybe_send_email
from .session_login import login_and_transfer_cookies


@dataclass
class MatchResult:
    product: ProductHit
    matched_keyword: str
    purchase_available: Optional[bool] = None
    area_item_no: Optional[str] = None
    added_to_cart: bool = False
    detail_note: str = ""


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
        self.seen_codes: Set[str] = set()

    def prepare(self) -> None:
        self.config.validate()
        self.client.bootstrap()
        if self.config.enable_add_to_cart:
            login_and_transfer_cookies(self.config, self.client)

    def run_once(self) -> RunReport:
        report = RunReport()
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
                print(f"[search] '{keyword}' -> {len(page_hits)} hits")
            except Exception as exc:  # noqa: BLE001
                msg = f"search failed for '{keyword}': {exc}"
                print(msg)
                report.errors.append(msg)

        # de-dupe by product code while preserving order
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

            result = MatchResult(product=hit, matched_keyword=fine)
            print(
                f"[match] {hit.sale_status} {hit.product_code} "
                f"{hit.display_name} (kw={fine})"
            )

            if self.config.enable_add_to_cart:
                self._try_add_to_cart(result, report)
                if result.added_to_cart and fine in self.remaining_targets:
                    self.remaining_targets.remove(fine)
            else:
                result.detail_note = "monitor-only"
                self.seen_codes.add(hit.product_code)

            report.matches.append(result)

        self._notify(report)
        return report

    def _try_add_to_cart(self, result: MatchResult, report: RunReport) -> None:
        product = result.product
        retries = self.config.add_cart_retry_count
        for attempt in range(1, retries + 1):
            try:
                detail = self.client.get_product(product.product_code)
                result.purchase_available = bool(detail.get("purchaseAvailable"))
                area_item_no = self.client.pick_area_item_no(detail)
                result.area_item_no = area_item_no
                if not result.purchase_available:
                    result.detail_note = "purchaseAvailable=false"
                    return
                if not area_item_no:
                    result.detail_note = "no areaItemNo"
                    return
                self.client.add_to_cart(area_item_no, qty=self.config.cart_qty)
                result.added_to_cart = True
                result.detail_note = f"added via {area_item_no}"
                self.seen_codes.add(product.product_code)
                print(f"[cart] added {product.product_code}")
                return
            except Exception as exc:  # noqa: BLE001
                result.detail_note = f"attempt {attempt}/{retries} failed: {exc}"
                print(f"[cart] {result.detail_note}")
                time.sleep(1)
        report.errors.append(
            f"failed to add {product.product_code}: {result.detail_note}"
        )

    def _notify(self, report: RunReport) -> None:
        if not report.matches and not report.errors:
            subject = "P-Bandai HK: no matches"
            body = (
                "P-Bandai HK notify\n"
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
                    f"  code={p.product_code} kw={match.matched_keyword}\n"
                    f"  {p.url}\n"
                    f"  note={match.detail_note}"
                )
            if report.errors:
                lines.append("")
                lines.append("Errors:")
                lines.extend(f"- {err}" for err in report.errors)
            body = "\n".join(lines) + "\n"

        status = maybe_send_email(self.config, subject, body)
        print(f"[email] {status}")

    def run_forever(self) -> None:
        self.prepare()
        if self.config.schedule_mode:
            if not self.config.execute_times:
                raise ValueError("SCHEDULE_MODE=1 requires EXECUTE_TIME")
            for when in self.config.execute_times:
                schedule.every().day.at(when).do(self.run_once)
                print(f"[schedule] registered {when}")
            print("[schedule] waiting...")
            while True:
                schedule.run_pending()
                time.sleep(self.config.schedule_polling_period)
        else:
            print("[loop] immediate mode")
            while True:
                report = self.run_once()
                if self.config.enable_add_to_cart and not self.remaining_targets:
                    print("[loop] all targets handled; exiting")
                    break
                if not self.config.enable_add_to_cart and report.matches:
                    # monitor mode keeps running, but can exit early if desired later
                    pass
                print(f"[loop] sleep {self.config.retry_wait}s")
                time.sleep(self.config.retry_wait)
