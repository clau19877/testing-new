from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import requests


@dataclass
class ProductHit:
    product_code: str
    area_product_no: str
    area_code: str
    name_en: str
    name_zh_hk: str
    sale_status: str
    product_type: str
    price_amount: Optional[float]
    currency: Optional[str]
    flags: List[str]
    url: str

    @property
    def display_name(self) -> str:
        return self.name_en or self.name_zh_hk or self.product_code

    def matches(self, keywords: Iterable[str]) -> Optional[str]:
        haystack = f"{self.name_en} {self.name_zh_hk} {self.product_code}".lower()
        for keyword in keywords:
            if keyword.lower() in haystack:
                return keyword
        return None


class PBandaiHkClient:
    """Thin client for p-bandai.com HK JSON APIs."""

    def __init__(
        self,
        base_url: str = "https://p-bandai.com",
        area_code: str = "hk",
        accept_language: str = "en",
        session: Optional[requests.Session] = None,
        proxy: str | None = None,
        name: str = "default",
    ) -> None:
        from .proxy_util import parse_proxy

        self.base_url = base_url.rstrip("/")
        self.area_code = area_code.lower()
        self.accept_language = accept_language
        self.name = name
        self.proxy = (proxy or "").strip()
        self.session = session or requests.Session()
        self.csrf_token: Optional[str] = None
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/134.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "X-G1-Area-Code": self.area_code,
                "Accept-Language": self.accept_language,
                "Referer": f"{self.base_url}/{self.area_code}/",
                "Content-Type": "application/json",
            }
        )
        parsed = parse_proxy(self.proxy)
        if parsed:
            self.session.proxies.update(parsed.requests_proxies())

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _auth_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.csrf_token:
            headers["X-CSRF-TOKEN"] = self.csrf_token
        return headers

    def bootstrap(self, *, required: bool = True) -> None:
        """Warm cookies / CSRF via public member context.

        WAF/proxies often return HTML for /api/context/member. When required=False,
        failures are logged and ignored so cookie reuse / browser login can continue.
        """
        import time

        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                home = self.session.get(
                    self._url(f"/{self.area_code}/"),
                    timeout=30,
                    headers={"Accept": "text/html,application/xhtml+xml,*/*"},
                )
                # Ignore home status — some regions return soft blocks with 200 HTML.
                _ = home.status_code
                self.refresh_csrf(required=True)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(0.6 * attempt)
        if required and last_exc is not None:
            raise RuntimeError(
                f"bootstrap failed after retries: {_short_exc(last_exc)}"
            ) from last_exc

    def refresh_csrf(self, *, required: bool = True) -> Optional[str]:
        """Fetch CSRF from /api/context/member; tolerate WAF HTML responses."""
        import time

        last_detail = ""
        for attempt in range(1, 4):
            try:
                resp = self.session.get(
                    self._url("/api/context/member"),
                    timeout=30,
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "Referer": f"{self.base_url}/{self.area_code}/",
                        "Origin": self.base_url,
                    },
                )
                data = _safe_json(resp)
                if data is None:
                    last_detail = (
                        f"status={resp.status_code} "
                        f"{_short_html_error(resp.text or '') or 'empty body'}"
                    )
                    time.sleep(0.5 * attempt)
                    continue
                token = data.get("csrfToken") or data.get("csrf") or data.get("token")
                if token:
                    self.csrf_token = str(token)
                    return self.csrf_token
                last_detail = f"member JSON missing csrfToken keys={list(data)[:8]}"
            except Exception as exc:  # noqa: BLE001
                last_detail = _short_exc(exc)
                time.sleep(0.5 * attempt)

        # Fallback: scrape csrf from any HTML page that loaded.
        scraped = self._csrf_from_html()
        if scraped:
            self.csrf_token = scraped
            return self.csrf_token

        if required:
            raise RuntimeError(
                f"refresh_csrf failed (WAF/proxy/non-JSON?): {last_detail}"
            )
        return self.csrf_token

    def _csrf_from_html(self) -> Optional[str]:
        import re

        try:
            resp = self.session.get(
                self._url(f"/{self.area_code}/"),
                timeout=30,
                headers={"Accept": "text/html,application/xhtml+xml,*/*"},
            )
            text = resp.text or ""
        except Exception:
            return None
        patterns = (
            r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
            r'content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']',
            r'name=["\']_csrf["\']\s+content=["\']([^"\']+)["\']',
            r'"csrfToken"\s*:\s*"([^"]+)"',
        )
        for pat in patterns:
            m = re.search(pat, text, flags=re.I)
            if m:
                return m.group(1)
        return None

    def search(
        self,
        keyword: str,
        *,
        limit: int = 40,
        offset: int = 0,
        product_statuses: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "keyword": keyword,
            "limit": limit,
            "offset": offset,
        }
        # HK search UI sends selected facet filters as `_f_<facet>=csv`.
        # Example: `_f_productStatuses=On,Waiting`
        if product_statuses:
            params["_f_productStatuses"] = ",".join(product_statuses)
        resp = self.session.get(self._url("/api/search"), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def iter_search_products(
        self,
        keyword: str,
        *,
        limit: int = 40,
        max_pages: int = 3,
        product_statuses: Optional[List[str]] = None,
    ) -> List[ProductHit]:
        hits: List[ProductHit] = []
        for page in range(max_pages):
            offset = page * limit
            payload = self.search(
                keyword,
                limit=limit,
                offset=offset,
                product_statuses=product_statuses,
            )
            results = payload.get("productResults") or {}
            products = results.get("products") or []
            allowed = {s.lower() for s in (product_statuses or [])}
            for raw in products:
                hit = self._to_hit(raw)
                # Keep a local guard; API facet filters can be ignored if mis-encoded.
                if allowed and hit.sale_status.lower() not in allowed:
                    continue
                hits.append(hit)
            total = int(results.get("totalCount") or 0)
            if offset + limit >= total or not products:
                break
        return hits

    def get_product(self, product_code: str) -> Dict[str, Any]:
        resp = self.session.get(self._url(f"/api/products/{product_code}"), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_products_bulk(self, product_codes: List[str], *, limit: int = 50) -> List[ProductHit]:
        if not product_codes:
            return []
        resp = self.session.get(
            self._url("/api/search/bulk"),
            params={
                "productCodes": ",".join(product_codes),
                "limit": limit,
                "excludeOutOfStock": "false",
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload if isinstance(payload, list) else payload.get("products") or []
        return [self._to_hit(raw) for raw in rows]

    def resolve_direct_product(self, product_code: str) -> tuple[ProductHit, Dict[str, Any]]:
        """Resolve a direct product code/link target into listing + detail payloads."""
        detail = self.get_product(product_code)
        bulk = self.get_products_bulk([product_code], limit=1)
        if bulk:
            hit = bulk[0]
        else:
            hit = self._hit_from_detail(detail, product_code)
        return hit, detail

    def _hit_from_detail(self, detail: Dict[str, Any], product_code: str) -> ProductHit:
        breadcrumb = detail.get("productBreadcrumb") or {}
        names = breadcrumb.get("productName") or {}
        info = detail.get("infoSection") or {}
        price_block = info.get("price") or info.get("priceInfo") or {}
        price = price_block.get("fixedListPrice") or price_block.get("sellingPrice") or {}
        general = info.get("generalProdInfo") or {}
        flags = list(detail.get("flags") or [])
        availability = str(general.get("availabilityStatus") or "")
        if detail.get("purchaseAvailable") or availability.lower() == "on":
            sale_status = "On"
        elif any(flag in {"PRE_ORDER_CLOSED", "END_OF_SALE"} for flag in flags):
            sale_status = "End"
        elif availability:
            sale_status = availability
        else:
            sale_status = "Waiting"
        return ProductHit(
            product_code=detail.get("productCode") or product_code,
            area_product_no=detail.get("areaProductNo") or "",
            area_code=detail.get("areaCode") or self.area_code.upper(),
            name_en=names.get("en") or (info.get("productName") or {}).get("en") or "",
            name_zh_hk=names.get("zh-HK") or (info.get("productName") or {}).get("zh-HK") or "",
            sale_status=sale_status,
            product_type=str(info.get("productType") or ""),
            price_amount=price.get("amount"),
            currency=price.get("currency"),
            flags=flags,
            url=f"{self.base_url}/{self.area_code}/item/{product_code}",
        )

    def cart_summary(self) -> Dict[str, Any]:
        resp = self.session.get(
            self._url("/api/cart/summary"),
            headers=self._auth_headers(),
            timeout=30,
        )
        data = _safe_json(resp)
        if data is None:
            raise RuntimeError(
                f"cart/summary non-JSON status={resp.status_code}: "
                f"{_short_html_error(resp.text or '') or 'empty body'}"
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"cart/summary failed ({resp.status_code}): {data}")
        return data

    def add_to_cart(
        self,
        area_item_no: str,
        qty: int = 1,
        event_pickup_specified_pickup_sn: Optional[int] = None,
        *,
        product_code: str = "",
    ) -> Dict[str, Any]:
        if not self.csrf_token:
            self.refresh_csrf()
        item: Dict[str, Any] = {"areaItemNo": area_item_no, "qty": qty}
        if event_pickup_specified_pickup_sn is not None:
            item["eventPickupSpecifiedPickupSn"] = event_pickup_specified_pickup_sn
        headers = self._auth_headers()
        if product_code:
            headers["Referer"] = f"{self.base_url}/{self.area_code}/item/{product_code}"
        headers.setdefault("Origin", self.base_url)
        resp = self.session.post(
            self._url("/api/cart/addToCart"),
            json=[item],
            headers=headers,
            timeout=30,
        )
        if resp.status_code >= 400:
            detail = _response_error_detail(resp)
            raise RuntimeError(f"addToCart failed ({resp.status_code}): {detail}")
        # Some gateways return 200 HTML by mistake; require JSON-ish body.
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" in ctype:
            raise RuntimeError(
                f"addToCart failed (unexpected HTML {resp.status_code}): "
                f"{_short_html_error(resp.text)}"
            )
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text[:500]}

    def pick_area_item_no(self, product_detail: Dict[str, Any]) -> Optional[str]:
        area_item_nos = product_detail.get("areaItemNos") or []
        if not area_item_nos:
            return None
        inventory = product_detail.get("areaItemInventoryInfoMap") or {}
        mapping = product_detail.get("areaItemToItemCode") or {}
        for area_item_no in area_item_nos:
            item_meta = mapping.get(area_item_no) or {}
            available = item_meta.get("availableQty")
            if available is not None and int(available) <= 0:
                continue
            inv = inventory.get(area_item_no) or {}
            # inventory map uses string keys like "0"
            if inv and all(int(v or 0) <= 0 for v in inv.values()):
                continue
            return area_item_no
        # fall back to first SKU when inventory fields are absent
        return area_item_nos[0]

    def _to_hit(self, raw: Dict[str, Any]) -> ProductHit:
        names = raw.get("productName") or {}
        price = raw.get("fixedListPrice") or {}
        code = raw.get("productCode") or ""
        return ProductHit(
            product_code=code,
            area_product_no=raw.get("areaProductNo") or "",
            area_code=raw.get("areaCode") or self.area_code.upper(),
            name_en=names.get("en") or "",
            name_zh_hk=names.get("zh-HK") or "",
            sale_status=raw.get("saleStatus") or "",
            product_type=raw.get("productType") or "",
            price_amount=price.get("amount"),
            currency=price.get("currency"),
            flags=list(raw.get("flags") or []),
            url=f"{self.base_url}/{self.area_code}/item/{code}",
        )


def _safe_json(resp: Any) -> Optional[Dict[str, Any]]:
    """Return parsed JSON object, or None for empty/HTML/non-JSON bodies."""
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        return None
    low = text[:200].lower()
    if low.startswith("<!doctype") or low.startswith("<html") or "page not available" in low:
        return None
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" in ctype and not text.startswith("{") and not text.startswith("["):
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _response_error_detail(resp: Any) -> str:
    data = _safe_json(resp)
    if data is not None:
        return str(data)
    return _short_html_error(resp.text or "")


def _short_html_error(text: str) -> str:
    low = (text or "").lower()
    if not (text or "").strip():
        return "empty body"
    if "<html" in low or "<!doctype" in low:
        if "page not available" in low:
            return "HTML WAF/error page (Page not available)"
        return f"HTML error page ({len(text)} chars)"
    return (text or "")[:300]


def _short_exc(exc: BaseException) -> str:
    msg = str(exc) or exc.__class__.__name__
    if "Expecting value" in msg or "JSONDecodeError" in msg:
        return "non-JSON response (WAF/proxy empty/HTML)"
    return msg[:300]
