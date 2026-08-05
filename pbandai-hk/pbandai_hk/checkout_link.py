"""Build a pasteable P-Bandai / Global-e checkout URL that carries cart tokens.

Bare `/checkout` links are session-cookie bound and fail in another browser.
Global-e exposes `GE_CART_TOKEN` (also used as `confirmationCartToken` query
param) and merchant cart id `GlobalECartId`. Prefer those in the Discord link.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .logging_utils import get_logger

logger = get_logger("checkout_link")

_PINNED_COOKIE_NAMES = (
    "GE_CART_TOKEN",
    "GlobalECartId",
    "GlobalE_Data",
    "SESSION",
    "JSESSIONID",
)

_EXTRACT_JS = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const cookieMap = () => {
  const out = {};
  try {
    String(document.cookie || '').split(';').forEach((part) => {
      const i = part.indexOf('=');
      if (i < 0) return;
      const k = part.slice(0, i).trim();
      const v = part.slice(i + 1).trim();
      if (k) out[k] = decodeURIComponent(v);
    });
  } catch (e) {}
  return out;
};

const storageMap = (store) => {
  const out = {};
  try {
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i);
      if (!k) continue;
      if (!/cart|token|global|checkout|session|ge_/i.test(k)) continue;
      out[k] = String(store.getItem(k) || '').slice(0, 500);
    }
  } catch (e) {}
  return out;
};

const collectUrls = () => {
  const urls = [];
  try { urls.push(String(location.href || '')); } catch (e) {}
  try {
    document.querySelectorAll('iframe[src],a[href]').forEach((el) => {
      const u = el.getAttribute('src') || el.getAttribute('href') || '';
      if (u) urls.push(u);
    });
  } catch (e) {}
  try {
    performance.getEntriesByType('resource').forEach((e) => {
      if (e && e.name) urls.push(String(e.name));
    });
  } catch (e) {}
  return urls;
};

const tryGetCheckoutUrl = async (merchantToken) => {
  try {
    const gem = window.GEM_Components && window.GEM_Components.ExternalMethodsComponent;
    if (!gem || typeof gem.GetCheckoutUrl !== 'function') return null;
    return await new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; resolve(v || null); } };
      try {
        gem.GetCheckoutUrl({ CartToken: merchantToken || '' }, finish);
      } catch (e) { finish(null); }
      setTimeout(() => finish(null), 4500);
    });
  } catch (e) { return null; }
};

const cookies = cookieMap();
const merchantToken = cookies.GlobalECartId || cookies.GlobalE_CartId || '';
const geUrl = await tryGetCheckoutUrl(merchantToken);
return {
  href: String(location.href || ''),
  title: String(document.title || ''),
  cookies,
  localStorage: storageMap(window.localStorage),
  sessionStorage: storageMap(window.sessionStorage),
  geCheckoutUrl: geUrl,
  urls: collectUrls().slice(0, 80),
};
"""


@dataclass
class PortableCheckout:
    """Checkout payload safe to paste into another browser."""

    payment_url: str
    portable: bool = False
    ge_cart_token: str = ""
    merchant_cart_token: str = ""
    country_code: str = ""
    currency_code: str = ""
    session_cookie: str = ""
    cookie_header: str = ""
    raw_url: str = ""
    source: str = ""
    notes: List[str] = field(default_factory=list)

    def discord_content(self, *, area: str, product_code: str, product_url: str, instance: str, note: str) -> str:
        lines = [
            f"**P-Bandai {area} cart success** `{instance}`",
            f"Product: `{product_code}`",
            f"Product URL: {product_url}",
            f"**Checkout (paste in browser):** {self.payment_url}",
        ]
        if self.portable:
            lines.append("_Link includes Global-e cart token — open in a fresh browser to pay._")
        else:
            lines.append(
                "_⚠️ Link may still need session cookies (token not captured). "
                "See embed fields / logs._"
            )
        if self.ge_cart_token:
            lines.append(f"`confirmationCartToken` = `{self.ge_cart_token}`")
        if self.merchant_cart_token:
            lines.append(f"`GlobalECartId` = `{self.merchant_cart_token}`")
        if note:
            lines.append(f"Note: {note}")
        return "\n".join(lines)[:1900]

    def discord_embed(self, *, area: str, product_code: str, product_url: str, instance: str) -> Dict[str, Any]:
        fields = [
            {"name": "Instance", "value": f"`{instance}`", "inline": True},
            {"name": "Area", "value": f"`{area}`", "inline": True},
            {
                "name": "Portable",
                "value": "yes ✅" if self.portable else "partial ⚠️",
                "inline": True,
            },
            {
                "name": "Checkout link",
                "value": self.payment_url[:1024] or "-",
                "inline": False,
            },
        ]
        if self.ge_cart_token:
            fields.append(
                {
                    "name": "confirmationCartToken / GE_CART_TOKEN",
                    "value": f"`{self.ge_cart_token[:200]}`",
                    "inline": False,
                }
            )
        if self.merchant_cart_token:
            fields.append(
                {
                    "name": "GlobalECartId",
                    "value": f"`{self.merchant_cart_token[:200]}`",
                    "inline": False,
                }
            )
        if self.cookie_header:
            fields.append(
                {
                    "name": "Cookies (Cookie-Editor import / backup)",
                    "value": f"`{self.cookie_header[:900]}`",
                    "inline": False,
                }
            )
        fields.append(
            {"name": "Product", "value": product_url[:1024] or product_code, "inline": False}
        )
        return {
            "title": f"Cart OK — {product_code}",
            "description": (
                "Paste the checkout link in another browser. "
                "If Global-e asks again, use the tokens/cookies below."
            )[:4096],
            "color": 5763719 if self.portable else 16776960,
            "fields": fields[:25],
        }


def _cookie_dict_from_driver(driver: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    # CDP gets httpOnly cookies (SESSION, often GE_CART_TOKEN).
    try:
        res = driver.execute_cdp_cmd("Network.getAllCookies", {})
        for c in res.get("cookies") or []:
            name = str(c.get("name") or "")
            if not name:
                continue
            domain = str(c.get("domain") or "").lower()
            if "p-bandai" in domain or "global-e" in domain or name in _PINNED_COOKIE_NAMES:
                out[name] = str(c.get("value") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        for c in driver.get_cookies() or []:
            name = str(c.get("name") or "")
            if name and name not in out:
                out[name] = str(c.get("value") or "")
    except Exception:  # noqa: BLE001
        pass
    return out


def _url_has_checkout_token(url: str) -> bool:
    low = (url or "").lower()
    if not low.startswith("http"):
        return False
    keys = (
        "confirmationcarttoken=",
        "carttoken=",
        "merchantcarttoken=",
        "ge_cart_token=",
        "globale cart",
        "token=",
    )
    if any(k in low for k in keys):
        # Avoid bare CSRF-looking short tokens alone on unrelated pages
        if "checkout" in low or "global-e" in low or "globale" in low or "payment" in low:
            return True
        if "confirmationcarttoken=" in low or "carttoken=" in low:
            return True
    return False


def _score_url(url: str) -> int:
    low = (url or "").lower()
    if not low.startswith("http"):
        return -100
    score = 0
    if "global-e.com" in low or "globale" in low:
        score += 50
    if "checkout" in low:
        score += 20
    if "payment" in low or "/pay" in low:
        score += 15
    if "confirmationcarttoken=" in low:
        score += 80
    if "carttoken=" in low:
        score += 70
    if "merchantcarttoken=" in low:
        score += 40
    if "p-bandai.com" in low and low.rstrip("/").endswith("/cart"):
        score -= 10
    if "page not available" in low:
        score -= 50
    return score


def _build_confirmation_url(
    *,
    base_url: str,
    area_code: str,
    ge_cart_token: str,
    country_code: str = "",
    currency_code: str = "",
    raw_url: str = "",
) -> str:
    token = (ge_cart_token or "").strip()
    if not token:
        return ""
    # Prefer mutating current checkout URL if already there.
    seed = raw_url if raw_url and "p-bandai.com" in raw_url and "/checkout" in raw_url else ""
    if not seed:
        seed = f"{base_url.rstrip('/')}/{area_code.strip().lower()}/checkout"
    parts = urlparse(seed)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["confirmationCartToken"] = token
    if country_code:
        q.setdefault("countryCode", country_code)
    if currency_code:
        q.setdefault("currencyCode", currency_code)
    return urlunparse(
        (
            parts.scheme or "https",
            parts.netloc,
            parts.path or f"/{area_code.strip().lower()}/checkout",
            parts.params,
            urlencode(q),
            "",
        )
    )


def _pick_country(area_code: str, cookies: Dict[str, str], href: str) -> str:
    q = dict(parse_qsl(urlparse(href).query, keep_blank_values=True))
    for key in ("countryCode", "CountryCode", "country"):
        if q.get(key):
            return str(q[key])
    # GlobalE_Data often JSON-ish
    raw = cookies.get("GlobalE_Data") or ""
    if raw:
        try:
            data = json.loads(raw)
            for key in ("countryISO", "countryCode", "CountryCode"):
                if data.get(key):
                    return str(data[key])
        except Exception:  # noqa: BLE001
            pass
    area = (area_code or "").upper()
    return {
        "US": "US",
        "HK": "HK",
        "TW": "TW",
        "SG": "SG",
        "EU": "FR",
        "FR": "FR",
        "AU": "AU",
        "NZ": "NZ",
    }.get(area, area or "US")


def _cookie_header(cookies: Dict[str, str]) -> str:
    parts = []
    for name in _PINNED_COOKIE_NAMES:
        val = cookies.get(name)
        if val:
            parts.append(f"{name}={val}")
    # Include a few more GE-related cookies if present.
    for name, val in cookies.items():
        if name in _PINNED_COOKIE_NAMES or not val:
            continue
        if name.lower().startswith("global") or name.upper().startswith("GE_"):
            parts.append(f"{name}={val}")
    return "; ".join(parts)[:1500]


def extract_portable_checkout(
    driver: Any,
    *,
    base_url: str,
    area_code: str,
    wait_seconds: float = 12.0,
) -> PortableCheckout:
    """Poll the live browser for a tokenized checkout URL."""
    deadline = time.time() + max(3.0, float(wait_seconds))
    best = PortableCheckout(
        payment_url=f"{base_url.rstrip('/')}/{area_code.strip().lower()}/checkout",
        raw_url="",
        source="fallback",
    )
    last_snap: Dict[str, Any] = {}

    while time.time() < deadline:
        cookies = _cookie_dict_from_driver(driver)
        try:
            snap = driver.execute_async_script(
                """
                const done = arguments[arguments.length - 1];
                (async () => {
                """
                + _EXTRACT_JS
                + """
                })().then(done).catch((e) => done({
                  error: String(e),
                  href: String(location.href || ''),
                  cookies: {},
                  urls: []
                }));
                """
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("checkout extract script failed: %s", exc)
            snap = {"href": "", "cookies": {}, "urls": []}

        if not isinstance(snap, dict):
            snap = {}
        last_snap = snap
        # Merge document cookies + CDP cookies (CDP wins for httpOnly).
        js_cookies = snap.get("cookies") if isinstance(snap.get("cookies"), dict) else {}
        merged = {**{str(k): str(v) for k, v in js_cookies.items()}, **cookies}

        href = str(snap.get("href") or "")
        try:
            href = href or str(driver.current_url or "")
        except Exception:  # noqa: BLE001
            pass

        ge_token = (
            merged.get("GE_CART_TOKEN")
            or dict(parse_qsl(urlparse(href).query)).get("confirmationCartToken")
            or ""
        )
        merchant = merged.get("GlobalECartId") or merged.get("GlobalE_CartId") or ""
        country = _pick_country(area_code, merged, href)
        currency = ""
        q = dict(parse_qsl(urlparse(href).query, keep_blank_values=True))
        currency = str(q.get("currencyCode") or q.get("CurrencyCode") or "")

        candidates: List[str] = []
        ge_url = str(snap.get("geCheckoutUrl") or "").strip()
        if ge_url:
            candidates.append(ge_url)
        candidates.append(href)
        for u in snap.get("urls") or []:
            if isinstance(u, str) and u.startswith("http"):
                candidates.append(u)

        # Synthesize confirmationCartToken URL when we have GE token.
        synthetic = _build_confirmation_url(
            base_url=base_url,
            area_code=area_code,
            ge_cart_token=ge_token,
            country_code=country,
            currency_code=currency,
            raw_url=href,
        )
        if synthetic:
            candidates.insert(0, synthetic)

        ranked = sorted(
            {(u or "").strip() for u in candidates if (u or "").strip()},
            key=_score_url,
            reverse=True,
        )
        top = ranked[0] if ranked else best.payment_url
        portable = _url_has_checkout_token(top) or bool(ge_token and synthetic and top == synthetic)

        best = PortableCheckout(
            payment_url=top or best.payment_url,
            portable=bool(portable),
            ge_cart_token=ge_token,
            merchant_cart_token=merchant,
            country_code=country,
            currency_code=currency,
            session_cookie=merged.get("SESSION") or "",
            cookie_header=_cookie_header(merged),
            raw_url=href,
            source="geCheckoutUrl" if ge_url and top == ge_url else (
                "confirmationCartToken" if synthetic and top == synthetic else "observed"
            ),
            notes=[],
        )
        if best.portable and best.ge_cart_token:
            logger.info(
                "portable checkout ready source=%s url=%s",
                best.source,
                best.payment_url[:160],
            )
            return best
        time.sleep(0.7)

    if not best.ge_cart_token and last_snap:
        best.notes.append("GE_CART_TOKEN not observed before timeout")
    logger.warning(
        "portable checkout incomplete portable=%s source=%s url=%s",
        best.portable,
        best.source,
        (best.payment_url or "")[:160],
    )
    return best
