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
from pbandai_hk.proxy_util import redact_proxy


def _dotenv_path(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    local = Path(__file__).resolve().parent / ".env"
    return str(local) if local.exists() else None


def cmd_once(config: Config) -> int:
    bot = PBandaiHkBot(config)
    try:
        bot.prepare()
        if bot.click_farm is not None:
            successes = bot.click_farm.run()
            print(
                json.dumps(
                    {
                        "mode": "click_farm",
                        "successes": len(successes),
                        "items": [
                            {
                                "instance": s.name,
                                "payment_url": s.payment_url,
                                "clicks": s.clicks,
                            }
                            for s in successes
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if successes else 1
        report = bot.run_once()
    finally:
        if bot.click_farm is not None:
            try:
                bot.click_farm.close()
            except Exception:  # noqa: BLE001
                pass
            bot.click_farm = None
        if bot.warm_pool is not None:
            try:
                bot.warm_pool.close()
            except Exception:  # noqa: BLE001
                pass
            bot.warm_pool = None
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
                        "session": m.session_name,
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
        proxy=config.proxy_url,
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
        proxy=config.proxy_url,
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
        "proxy": redact_proxy(config.proxy_url) or None,
        "price": {
            "amount": hit.price_amount,
            "currency": hit.currency,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info(
        "checked %s status=%s purchasable=%s",
        code,
        hit.sale_status,
        detail.get("purchaseAvailable"),
    )
    return 0


def cmd_sessions(config: Config) -> int:
    from pbandai_hk.sessions import load_session_specs

    setup_logging(config.log_file, level=config.log_level)
    specs = load_session_specs(config.sessions_file)
    if not specs:
        print(f"No sessions in {config.sessions_file}")
        print("Create one with: python web_shopping_bot_hk.py login --name acc1 --proxy http://user:pass@host:port")
        return 0
    print(f"Sessions file: {config.sessions_file}")
    for spec in specs:
        print(
            f"- {spec.name} enabled={spec.enabled} "
            f"proxy={redact_proxy(spec.proxy) or '-'} cookie_file={spec.cookie_file or '-'}"
        )
    return 0


def cmd_login(config: Config, name: str, proxy: str, force: bool) -> int:
    print("Login removed — use guest click farm instead.")
    print("Set in .env: ENABLE_ADD_TO_CART=1 CLICK_FARM=1 BROWSER_INSTANCES=20")
    print("             CLICK_AT_SECOND=0 DISCORD_WEBHOOK_URL=<webhook>")
    print("Then: python web_shopping_bot_hk.py loop")
    _ = (config, name, proxy, force)
    return 2


def cmd_tasks(config: Config, force: bool) -> int:
    print("Login / task.csv removed — use guest click farm instead.")
    print("Set CLICK_FARM=1 and optional proxy.csv, then run: loop")
    _ = (config, force)
    return 2


def cmd_diagnose(config: Config, link_or_code: str) -> int:
    """Full cart-eligibility diagnosis for one product (logs + JSON snapshot)."""
    from pbandai_hk.api import PBandaiHkClient
    from pbandai_hk.diagnostics import format_signals_text, is_cart_eligible, log_cart_diagnosis
    from pbandai_hk.sessions import build_runtime_sessions

    setup_logging(config.log_file, level="DEBUG")
    logger = get_logger("cli")
    code = extract_product_code(link_or_code)
    if not code:
        logger.error("could not parse product code from: %s", link_or_code)
        return 1

    sessions = build_runtime_sessions(
        sessions_file=config.sessions_file,
        base_url=config.base_url,
        area_code=config.area_code,
        accept_language=config.accept_language,
        fallback_proxy=config.proxy_url,
        cookie_file=config.cookie_file,
    )
    # Prefer first logged-in session; fall back to anonymous client.
    clients = [runtime.client for runtime in sessions] or [
        PBandaiHkClient(
            base_url=config.base_url,
            area_code=config.area_code,
            accept_language=config.accept_language,
            proxy=config.proxy_url,
            name="anonymous",
        )
    ]

    exit_code = 0
    for client in clients:
        try:
            client.bootstrap()
            hit, detail = client.resolve_direct_product(code)
            picked = client.pick_area_item_no(detail) or ""
            signals = is_cart_eligible(
                detail,
                product_code=code,
                sale_status=hit.sale_status,
                picked_area_item_no=picked,
            )
            path = log_cart_diagnosis(
                signals,
                session_name=client.name,
                stage="diagnose-cmd",
            )
            print("=" * 60)
            print(f"session={client.name} proxy={redact_proxy(client.proxy) or '-'}")
            print(format_signals_text(signals))
            print(f"snapshot={path}")
            print(f"log_file={config.log_file}")
            if signals.cart_eligible:
                print("RESULT: cart should be attempted (even if purchaseAvailable=false)")
            else:
                print("RESULT: cart blocked by hard reasons above")
                exit_code = 1
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            log_exception(logger, f"diagnose failed session={client.name}", exc)
            print(f"ERROR session={client.name}: {exc}", file=sys.stderr)
    return exit_code


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
    sub.add_parser("sessions", help="List configured multi-sessions")
    login_p = sub.add_parser(
        "login",
        help="Removed — use CLICK_FARM=1 guest click farm (no login)",
    )
    login_p.add_argument("--name", required=True, help="Session name, e.g. acc1")
    login_p.add_argument(
        "--proxy",
        default="",
        help="Proxy URL, e.g. http://user:pass@host:8080 or socks5://host:1080",
    )
    login_p.add_argument(
        "--force",
        action="store_true",
        help="Force browser login even if cookie file exists",
    )
    tasks_p = sub.add_parser(
        "tasks",
        help="Removed — use CLICK_FARM=1 guest click farm (no task.csv login)",
    )
    tasks_p.add_argument(
        "--force",
        action="store_true",
        help="Force browser login even if cookie files already exist",
    )
    diagnose_p = sub.add_parser(
        "diagnose",
        help="Full cart-eligibility diagnosis for one product (writes logs/diagnostics/*.json)",
    )
    diagnose_p.add_argument("link", help="Direct product URL or product code")

    args = parser.parse_args(argv)
    config = Config.from_env(_dotenv_path(args.dotenv_path))

    command = args.command or "once"
    try:
        if command == "search":
            return cmd_search(config, args.keyword)
        if command == "check":
            return cmd_check(config, args.link)
        if command == "diagnose":
            return cmd_diagnose(config, args.link)
        if command == "sessions":
            return cmd_sessions(config)
        if command == "login":
            return cmd_login(config, args.name, args.proxy, args.force)
        if command == "tasks":
            return cmd_tasks(config, args.force)
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
