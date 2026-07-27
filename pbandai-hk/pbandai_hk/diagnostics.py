from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logging_utils import get_logger

logger = get_logger("diagnostics")


@dataclass
class CartSignals:
    product_code: str
    purchase_available: Optional[bool] = None
    campaign_purchase_available: Optional[bool] = None
    discontinued: Optional[bool] = None
    availability_status: str = ""
    out_of_stock: Optional[bool] = None
    sale_status: str = ""
    display_status: str = ""
    product_type: str = ""
    flags: List[str] = field(default_factory=list)
    area_item_nos: List[str] = field(default_factory=list)
    picked_area_item_no: str = ""
    available_qty: Optional[int] = None
    inventory_sum: Optional[int] = None
    min_qty: Optional[int] = None
    max_qty: Optional[int] = None
    order_start: str = ""
    order_end: str = ""
    pre_order_status: str = ""
    cart_eligible: bool = False
    decision_reasons: List[str] = field(default_factory=list)
    blocking_reasons: List[str] = field(default_factory=list)
    raw_excerpt: Dict[str, Any] = field(default_factory=dict)


def extract_cart_signals(
    detail: Dict[str, Any],
    *,
    product_code: str = "",
    sale_status: str = "",
    picked_area_item_no: str = "",
) -> CartSignals:
    info = detail.get("infoSection") or {}
    general = info.get("generalProdInfo") or {}
    campaign = info.get("campaignInfo") or {}
    quantity = info.get("quantityInfo") or {}
    order = info.get("orderInfo") or {}
    mapping = detail.get("areaItemToItemCode") or {}
    inventory = detail.get("areaItemInventoryInfoMap") or {}
    area_item_nos = list(detail.get("areaItemNos") or [])

    available_qty: Optional[int] = None
    inventory_sum: Optional[int] = None
    qty_values: List[int] = []
    inv_values: List[int] = []
    for area_item_no in area_item_nos:
        meta = mapping.get(area_item_no) or {}
        if meta.get("availableQty") is not None:
            try:
                qty_values.append(int(meta.get("availableQty")))
            except (TypeError, ValueError):
                pass
        inv = inventory.get(area_item_no) or {}
        for value in inv.values():
            try:
                inv_values.append(int(value or 0))
            except (TypeError, ValueError):
                pass
    if qty_values:
        available_qty = max(qty_values)
    if inv_values:
        inventory_sum = sum(inv_values)

    signals = CartSignals(
        product_code=product_code or str(detail.get("productCode") or ""),
        purchase_available=_as_optional_bool(detail.get("purchaseAvailable")),
        campaign_purchase_available=_as_optional_bool(campaign.get("purchaseAvailable")),
        discontinued=_as_optional_bool(detail.get("discontinued") if "discontinued" in detail else info.get("discontinued")),
        availability_status=str(general.get("availabilityStatus") or ""),
        out_of_stock=_as_optional_bool(general.get("outOfStock")),
        sale_status=sale_status or str((detail.get("saleStatus") or "")),
        display_status=str(detail.get("displayStatus") or ""),
        product_type=str(info.get("productType") or detail.get("productType") or ""),
        flags=list(detail.get("flags") or []),
        area_item_nos=area_item_nos,
        picked_area_item_no=picked_area_item_no,
        available_qty=available_qty,
        inventory_sum=inventory_sum,
        min_qty=_as_optional_int(quantity.get("minQuantity")),
        max_qty=_as_optional_int(quantity.get("maxQuantity")),
        order_start=str(order.get("orderStartDate") or ""),
        order_end=str(order.get("orderEndDate") or ""),
        pre_order_status=str(order.get("preOrderStatus") or ""),
        raw_excerpt={
            "purchaseAvailable": detail.get("purchaseAvailable"),
            "flags": detail.get("flags"),
            "discontinued": detail.get("discontinued"),
            "areaItemNos": area_item_nos,
            "areaItemToItemCode": mapping,
            "areaItemInventoryInfoMap": inventory,
            "generalProdInfo": general,
            "campaignInfo": campaign,
            "quantityInfo": quantity,
            "orderInfo": order,
        },
    )
    _evaluate_eligibility(signals)
    return signals


def _evaluate_eligibility(signals: CartSignals) -> None:
    reasons: List[str] = []
    blocking: List[str] = []

    if signals.discontinued is True:
        blocking.append("discontinued=true")
    else:
        reasons.append("not discontinued")

    if signals.out_of_stock is True:
        blocking.append("outOfStock=true")
    elif signals.out_of_stock is False:
        reasons.append("outOfStock=false")

    status = (signals.availability_status or signals.sale_status or "").lower()
    if status and status not in {"on", "waiting"}:
        blocking.append(f"availability/sale status={status or '-'}")
    elif status:
        reasons.append(f"status={status}")

    if signals.available_qty is not None:
        if signals.available_qty <= 0:
            blocking.append(f"availableQty={signals.available_qty}")
        else:
            reasons.append(f"availableQty={signals.available_qty}")
    if signals.inventory_sum is not None:
        if signals.inventory_sum <= 0:
            blocking.append(f"inventory_sum={signals.inventory_sum}")
        else:
            reasons.append(f"inventory_sum={signals.inventory_sum}")

    if not signals.area_item_nos:
        blocking.append("no areaItemNos")
    else:
        reasons.append(f"areaItemNos={len(signals.area_item_nos)}")

    if signals.picked_area_item_no:
        reasons.append(f"picked={signals.picked_area_item_no}")
    elif signals.area_item_nos:
        blocking.append("could not pick areaItemNo")

    if signals.max_qty is not None and signals.max_qty <= 0:
        blocking.append(f"maxQuantity={signals.max_qty}")
    elif signals.max_qty:
        reasons.append(f"maxQuantity={signals.max_qty}")

    # Pre-order gate: do not fire cart before the order window opens.
    # Example A2891018001: preOrderStatus=NotStarted until orderStartDate.
    pre_status = (signals.pre_order_status or "").strip().lower()
    if pre_status:
        reasons.append(f"preOrderStatus={signals.pre_order_status}")
    if pre_status in {"notstarted", "not_started", "beforestart", "before_start"}:
        blocking.append(
            f"preOrderStatus={signals.pre_order_status} (order not open yet)"
        )
    elif signals.order_start:
        start_dt = _parse_iso(signals.order_start)
        if start_dt is not None:
            now = datetime.now(timezone.utc)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if now < start_dt:
                blocking.append(
                    f"orderStartDate={signals.order_start} (opens in "
                    f"{int((start_dt - now).total_seconds())}s)"
                )
            else:
                reasons.append(f"orderStartDate reached ({signals.order_start})")

    if signals.order_end:
        end_dt = _parse_iso(signals.order_end)
        if end_dt is not None:
            now = datetime.now(timezone.utc)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if now > end_dt:
                blocking.append(f"orderEndDate passed ({signals.order_end})")

    # Important: website can still allow cart when top-level purchaseAvailable=false
    # *after* the order window is open (inventory-driven). Keep as soft signal only.
    if signals.purchase_available is True:
        reasons.append("purchaseAvailable=true")
    elif signals.purchase_available is False:
        reasons.append(
            "purchaseAvailable=false (soft signal; site may still allow cart via inventory)"
        )

    if signals.campaign_purchase_available is False:
        reasons.append("campaignInfo.purchaseAvailable=false (ignored for normal cart)")

    signals.decision_reasons = reasons
    signals.blocking_reasons = blocking
    signals.cart_eligible = not blocking


def seconds_until_order_start(detail: Dict[str, Any]) -> Optional[float]:
    """Return seconds until orderStartDate, or None if unknown/already open."""
    info = detail.get("infoSection") or {}
    order = info.get("orderInfo") or {}
    start = _parse_iso(str(order.get("orderStartDate") or ""))
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    delta = (start - datetime.now(timezone.utc)).total_seconds()
    return delta if delta > 0 else 0.0


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def is_cart_eligible(
    detail: Dict[str, Any],
    *,
    product_code: str = "",
    sale_status: str = "",
    picked_area_item_no: str = "",
) -> CartSignals:
    return extract_cart_signals(
        detail,
        product_code=product_code,
        sale_status=sale_status,
        picked_area_item_no=picked_area_item_no,
    )


def log_cart_diagnosis(
    signals: CartSignals,
    *,
    session_name: str = "",
    stage: str = "cart-check",
    dump_dir: str | Path = "logs/diagnostics",
) -> Path:
    """Write a full diagnosis to the log and a JSON snapshot file."""
    payload = asdict(signals)
    payload["session"] = session_name
    payload["stage"] = stage
    payload["logged_at"] = datetime.now(timezone.utc).isoformat()

    logger.info(
        "[diagnose:%s] session=%s code=%s eligible=%s purchaseAvailable=%s "
        "availability=%s outOfStock=%s availableQty=%s inventory=%s picked=%s",
        stage,
        session_name or "-",
        signals.product_code,
        signals.cart_eligible,
        signals.purchase_available,
        signals.availability_status or signals.sale_status or "-",
        signals.out_of_stock,
        signals.available_qty,
        signals.inventory_sum,
        signals.picked_area_item_no or "-",
    )
    if signals.decision_reasons:
        logger.info(
            "[diagnose:%s] reasons: %s",
            stage,
            " | ".join(signals.decision_reasons),
        )
    if signals.blocking_reasons:
        logger.warning(
            "[diagnose:%s] blocking: %s",
            stage,
            " | ".join(signals.blocking_reasons),
        )
    else:
        logger.info("[diagnose:%s] no hard blockers; cart should be attempted", stage)

    # Full JSON always goes to debug for the log file.
    logger.debug("[diagnose:%s] full=%s", stage, json.dumps(payload, ensure_ascii=False))

    out_dir = Path(dump_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_part = session_name or "na"
    path = out_dir / f"{signals.product_code}_{session_part}_{stage}_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("[diagnose:%s] snapshot -> %s", stage, path)
    return path


def format_signals_text(signals: CartSignals) -> str:
    lines = [
        f"product={signals.product_code}",
        f"cart_eligible={signals.cart_eligible}",
        f"purchaseAvailable={signals.purchase_available}",
        f"campaign.purchaseAvailable={signals.campaign_purchase_available}",
        f"availabilityStatus={signals.availability_status or '-'}",
        f"saleStatus={signals.sale_status or '-'}",
        f"outOfStock={signals.out_of_stock}",
        f"discontinued={signals.discontinued}",
        f"availableQty={signals.available_qty}",
        f"inventory_sum={signals.inventory_sum}",
        f"areaItemNos={signals.area_item_nos}",
        f"picked={signals.picked_area_item_no or '-'}",
        f"qtyRange={signals.min_qty}->{signals.max_qty}",
        f"order={signals.order_start or '-'} .. {signals.order_end or '-'}",
        f"preOrderStatus={signals.pre_order_status or '-'}",
        f"flags={signals.flags}",
        "reasons:",
    ]
    for reason in signals.decision_reasons:
        lines.append(f"  + {reason}")
    if signals.blocking_reasons:
        lines.append("blocking:")
        for reason in signals.blocking_reasons:
            lines.append(f"  - {reason}")
    return "\n".join(lines)


def _as_optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _as_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
