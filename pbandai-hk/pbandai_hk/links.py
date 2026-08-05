from __future__ import annotations

import re
from typing import Iterable, List, Optional
from urllib.parse import urlparse

# Matches /hk/item/A2742450001 or /item/A2742450001
_ITEM_PATH_RE = re.compile(
    r"(?:/(?:[a-z]{2})/)?item/([A-Za-z][A-Za-z0-9]+)(?:[/?#]|$)",
    re.IGNORECASE,
)
_AREA_PATH_RE = re.compile(r"/([a-z]{2})/item/", re.IGNORECASE)
_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*\d[A-Za-z0-9]*$")


def extract_product_code(value: str) -> Optional[str]:
    """Extract a product code from a direct HK item URL or bare code."""
    text = (value or "").strip()
    if not text:
        return None

    if _CODE_RE.fullmatch(text):
        return text

    parsed = urlparse(text)
    path = parsed.path if (parsed.scheme or parsed.netloc) else text
    match = _ITEM_PATH_RE.search(path)
    if match:
        return match.group(1)

    segment = path.rstrip("/").split("/")[-1]
    if segment and _CODE_RE.fullmatch(segment):
        return segment
    return None


def extract_area_code(value: str) -> Optional[str]:
    """Extract the store area (e.g. `hk`) from a direct item URL."""
    text = (value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    path = parsed.path if (parsed.scheme or parsed.netloc) else text
    match = _AREA_PATH_RE.search(path)
    return match.group(1).lower() if match else None


def extract_area_codes(values: Iterable[str]) -> List[str]:
    areas: List[str] = []
    for value in values:
        area = extract_area_code(value)
        if area and area not in areas:
            areas.append(area)
    return areas


def extract_product_codes(values: Iterable[str]) -> List[str]:
    codes: List[str] = []
    seen = set()
    for value in values:
        code = extract_product_code(value)
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes
