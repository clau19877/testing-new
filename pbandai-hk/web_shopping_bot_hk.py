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
    from pbandai_hk.api import PBandaiHkClient
    from pbandai_hk.session_login import login_and_transfer_cookies
    from pbandai_hk.sessions import SessionSpec, upsert_session_spec

    setup_logging(config.log_file, level=config.log_level)
    logger = get_logger("cli")
    proxy = (proxy or config.proxy_url or "").strip()
    cookie_file = f"sessions/{name}.cookies.json"
    client = PBandaiHkClient(
        base_url=config.base_url,
        area_code=config.area_code,
        accept_language=config.accept_language,
        proxy=proxy,
        name=name,
    )
    try:
        client.bootstrap()
        login_and_transfer_cookies(
            config,
            client,
            proxy=proxy,
            save_cookie_file=cookie_file,
            force_browser=force or True,
        )
        upsert_session_spec(
            config.sessions_file,
            SessionSpec(
                name=name,
                enabled=True,
                proxy=proxy,
                cookie_file=cookie_file,
            ),
        )
        print(f"Saved session '{name}' -> {config.sessions_file}")
        print(f"Cookies -> {cookie_file}")
        return 0
    except Exception as exc:  # noqa: BLE001
        log_exception(logger, f"login failed for session={name}", exc)
        return 1


def cmd_tasks(config: Config, force: bool) -> int:
    """Login all rows from task.csv in parallel; each picks a random proxy.csv entry."""
    from pbandai_hk.task_runner import ensure_csv_templates, run_tasks_parallel

    setup_logging(config.log_file, level=config.log_level)
    logger = get_logger("cli")
    ready, messages = ensure_csv_templates(config)
    for msg in messages:
        print(msg)
    if not ready:
        return 1
    try:
        results = run_tasks_parallel(config, force=force)
    except Exception as exc:  # noqa: BLE001
        log_exception(logger, "tasks command failed", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "\nHint: proxy.csv accepts lines like:\n"
            "  host:port:user:pass\n"
            "  http://user:pass@host:port\n"
            "  socks5://host:1080\n",
            file=sys.stderr,
        )
        return 1

    print(f"Sessions file: {config.sessions_file}")
    ok = sum(1 for r in results if r.ok)
    for result in sorted(results, key=lambda r: r.name):
        status = "ok" if result.ok else f"fail: {result.error}"
        print(
            f"- {result.name} {status} "
            f"proxy={redact_proxy(result.proxy) or '-'} "
            f"cookie={result.cookie_file or '-'}"
        )
    return 0 if ok == len(results) and results else 1


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
        help="Interactive browser login for one named session (optional proxy)",
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
        help="Login all task.csv accounts in parallel; each picks a random proxy.csv row",
    )
    tasks_p.add_argument(
        "--force",
        action="store_true",
        help="Force browser login even if cookie files already exist",
    )

    args = parser.parse_args(argv)
    config = Config.from_env(_dotenv_path(args.dotenv_path))

    command = args.command or "once"
    try:
        if command == "search":
            return cmd_search(config, args.keyword)
        if command == "check":
            return cmd_check(config, args.link)
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
