#!/usr/bin/env python3
"""P-Bandai Hong Kong module entrypoint.

Inspired by jsakamoto65535/prebanClawler2, rewritten against the HK Vue/API storefront
(p-bandai.com/hk) instead of JP DOM scraping.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pbandai_hk.bot import PBandaiHkBot
from pbandai_hk.config import Config
from pbandai_hk.links import extract_product_code
from pbandai_hk.logging_utils import get_logger, log_exception, setup_logging


def _dotenv_path(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    local = Path(__file__).resolve().parent / ".env"
    return str(local) if local.exists() else None


def cmd_once(config: Config) -> int:
    bot = PBandaiHkBot(config)
    bot.prepare()
    report = bot.run_once()
    print(
        json.dumps(
            {
                "scanned": report.scanned,
                "matches": len(report.matches),
                "added": report.added_count,
                "errors": report.errors,
                "items": [
                    {
                        "code": m.product.product_code,
                        "name": m.product.display_name,
                        "status": m.product.sale_status,
                        "url": m.product.url,
                        "source": m.source,
                        "keyword": m.matched_keyword,
                        "added": m.added_to_cart,
                        "note": m.detail_note,
                    }
                    for m in report.matches
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if report.errors and not report.matches else 0


def cmd_loop(config: Config) -> int:
    bot = PBandaiHkBot(config)
    bot.run_forever()
    return 0


def cmd_search(config: Config, keyword: str) -> int:
    from pbandai_hk.api import PBandaiHkClient

    setup_logging(config.log_file, level=config.log_level)
    logger = get_logger("cli")
    client = PBandaiHkClient(
        base_url=config.base_url,
        area_code=config.area_code,
        accept_language=config.accept_language,
    )
    try:
        client.bootstrap()
        hits = client.iter_search_products(
            keyword,
            limit=config.search_limit,
            max_pages=1,
            product_statuses=config.sale_statuses,
        )
    except Exception as exc:  # noqa: BLE001
        log_exception(logger, f"search command failed for '{keyword}'", exc)
        return 1

    for hit in hits:
        price = ""
        if hit.price_amount is not None:
            price = f" | {hit.price_amount} {hit.currency or ''}".rstrip()
        print(f"[{hit.sale_status}] {hit.product_code} {hit.display_name}{price}")
        print(f"  {hit.url}")
    print(f"total_returned={len(hits)}")
    return 0


def cmd_check(config: Config, link_or_code: str) -> int:
    """Check one direct product URL/code."""
    from pbandai_hk.api import PBandaiHkClient

    setup_logging(config.log_file, level=config.log_level)
    logger = get_logger("cli")
    code = extract_product_code(link_or_code)
    if not code:
        logger.error("could not parse product code from: %s", link_or_code)
        return 1

    client = PBandaiHkClient(
        base_url=config.base_url,
        area_code=config.area_code,
        accept_language=config.accept_language,
    )
    try:
        client.bootstrap()
        hit, detail = client.resolve_direct_product(code)
    except Exception as exc:  # noqa: BLE001
        log_exception(logger, f"check failed for '{link_or_code}'", exc)
        return 1

    payload = {
        "input": link_or_code,
        "productCode": hit.product_code,
        "name": hit.display_name,
        "saleStatus": hit.sale_status,
        "purchaseAvailable": detail.get("purchaseAvailable"),
        "areaItemNo": client.pick_area_item_no(detail),
        "flags": hit.flags,
        "url": hit.url,
        "price": {
            "amount": hit.price_amount,
            "currency": hit.currency,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info("checked %s status=%s purchasable=%s", code, hit.sale_status, detail.get("purchaseAvailable"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P-Bandai HK monitor / cart helper")
    parser.add_argument(
        "--env",
        dest="dotenv_path",
        help="Path to .env file (default: ./pbandai-hk/.env if present)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("once", help="Run a single scan (default)")
    sub.add_parser("loop", help="Run immediate/schedule loop from .env")
    search_p = sub.add_parser("search", help="Ad-hoc keyword search against HK API")
    search_p.add_argument("keyword", help="Search keyword")
    check_p = sub.add_parser(
        "check",
        help="Check one direct product URL or code (e.g. https://p-bandai.com/hk/item/A2742450001)",
    )
    check_p.add_argument("link", help="Direct product URL or product code")

    args = parser.parse_args(argv)
    config = Config.from_env(_dotenv_path(args.dotenv_path))

    command = args.command or "once"
    try:
        if command == "search":
            return cmd_search(config, args.keyword)
        if command == "check":
            return cmd_check(config, args.link)
        if command == "loop":
            return cmd_loop(config)
        return cmd_once(config)
    except Exception as exc:  # noqa: BLE001
        setup_logging(config.log_file, level=config.log_level)
        log_exception(get_logger("cli"), f"command '{command}' failed", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
