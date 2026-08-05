"""Export a pasteable Global-e checkout URL from a held cart (no checkout click).

After addToCart succeeds the guest cart is already held. We read SESSION /
cart ids from the current page, call Global-e GetCartToken, and build:

  /{area}/checkout?confirmationCartToken=<GE_TOKEN>&countryCode=XX

Never navigate to /cart or /checkout for export. Never treat GE CSS/JS
includes as checkout links.
"""

from __future__ import annotations

import json
import re
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

_MERCHANT_IDS = {
    "us": "1958",
    "hk": "1925",
    "tw": "1926",
    "sg": "1927",
    "eu": "1959",
    "fr": "1959",
    "au": "1960",
    "nz": "1961",
}

_ASSET_HINTS = (
    "/includes/css/",
    "/includes/js/",
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".woff",
    ".woff2",
    ".map",
    "favicon",
)

# Runs entirely on the current page (PDP). No navigation.
_EXPORT_JS = r"""
const area = String(arguments[0] || 'hk').toLowerCase();
const merchantId = String(arguments[1] || '');
const done = arguments[arguments.length - 1];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const cookieMap = () => {
  const out = {};
  try {
    String(document.cookie || '').split(';').forEach((part) => {
      const i = part.indexOf('=');
      if (i < 0) return;
      const k = part.slice(0, i).trim();
      const v = part.slice(i + 1).trim();
      if (k) {
        try { out[k] = decodeURIComponent(v); }
        catch (e) { out[k] = v; }
      }
    });
  } catch (e) {}
  return out;
};

const collectIds = (node, out, depth) => {
  if (depth > 6 || node == null) return;
  if (typeof node === 'string' || typeof node === 'number') {
    const s = String(node);
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) out.push(s);
    if (/^[A-Za-z0-9_-]{16,80}$/.test(s) && /cart|session|token/i.test(s)) out.push(s);
    return;
  }
  if (Array.isArray(node)) {
    node.slice(0, 40).forEach((v) => collectIds(v, out, depth + 1));
    return;
  }
  if (typeof node === 'object') {
    Object.keys(node).forEach((k) => {
      const v = node[k];
      if (/^(cartId|cartID|cart_id|merchantCartToken|MerchantCartToken|cartToken|CartToken|id)$/i.test(k)) {
        if (v != null && v !== '') out.push(String(v));
      }
      collectIds(v, out, depth + 1);
    });
  }
};

const jsonpGetCartToken = (params) => new Promise((resolve) => {
  const cb = 'ge_cb_' + Date.now() + '_' + Math.floor(Math.random() * 1e6);
  let finished = false;
  const finish = (data) => {
    if (finished) return;
    finished = true;
    try { delete window[cb]; } catch (e) {}
    try { if (script && script.parentNode) script.parentNode.removeChild(script); } catch (e) {}
    resolve(data || null);
  };
  const q = Object.keys(params).map((k) => {
    const v = params[k];
    if (v == null || v === '') return '';
    return encodeURIComponent(k) + '=' + encodeURIComponent(String(v));
  }).filter(Boolean).join('&');
  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://gepi.global-e.com/Checkout/GetCartToken?' + q + '&jsoncallback=' + cb;
  window[cb] = (data) => finish(data);
  script.onerror = () => finish(null);
  setTimeout(() => finish(null), 8000);
  (document.head || document.documentElement).appendChild(script);
});

const fetchJson = async (url) => {
  try {
    const resp = await fetch(url, {
      credentials: 'include',
      headers: { 'Accept': 'application/json' },
    });
    const ct = String(resp.headers.get('content-type') || '');
    if (!resp.ok || ct.indexOf('json') < 0) {
      return { ok: false, status: resp.status, ct, body: null };
    }
    return { ok: true, status: resp.status, ct, body: await resp.json() };
  } catch (e) {
    return { ok: false, status: 0, ct: '', body: null, error: String(e) };
  }
};

(async () => {
  const cookies = cookieMap();
  const session = cookies.SESSION || '';
  let sessionUuid = '';
  try { sessionUuid = session ? atob(session) : ''; } catch (e) { sessionUuid = ''; }

  let country = area.toUpperCase() === 'EU' ? 'FR' : area.toUpperCase();
  let currency = ({
    US: 'USD', HK: 'HKD', TW: 'TWD', SG: 'SGD', FR: 'EUR', EU: 'EUR', AU: 'AUD', NZ: 'NZD'
  })[country] || 'USD';
  let culture = 'en-GB';
  try {
    const ge = JSON.parse(cookies.GlobalE_Data || '{}');
    if (ge.countryISO) country = String(ge.countryISO);
    if (ge.CurrencyCode) currency = String(ge.CurrencyCode);
    if (ge.CultureCode) culture = String(ge.CultureCode);
  } catch (e) {}

  const idHints = [];
  if (session) idHints.push(session);
  if (sessionUuid) idHints.push(sessionUuid);
  if (cookies.GlobalECartId) idHints.push(cookies.GlobalECartId);
  if (cookies.GE_CART_TOKEN) idHints.push(cookies.GE_CART_TOKEN);

  // Mine cart ids from summary / globale cart APIs (no navigation).
  const summaryAttempts = [];
  for (const path of ['/api/cart/summary', '/' + area + '/api/cart/summary']) {
    const got = await fetchJson(path);
    summaryAttempts.push({ path, status: got.status, ok: !!got.ok });
    if (got.ok && got.body) collectIds(got.body, idHints, 0);
  }
  for (const tok of [session, sessionUuid].filter(Boolean)) {
    const path = '/api/cart/external/globale/carts/' + encodeURIComponent(tok);
    const got = await fetchJson(path);
    summaryAttempts.push({ path, status: got.status, ok: !!got.ok });
    if (got.ok && got.body) collectIds(got.body, idHints, 0);
  }

  // DOM merchantcarttoken attrs if somehow present on PDP widgets.
  try {
    document.querySelectorAll('[merchantcarttoken]').forEach((el) => {
      const v = el.getAttribute('merchantcarttoken');
      if (v) idHints.push(v);
    });
  } catch (e) {}

  const candidates = [];
  idHints.forEach((v) => {
    const s = String(v || '').trim();
    if (s && candidates.indexOf(s) < 0) candidates.push(s);
  });

  const tokenAttempts = [];
  let geCartToken = cookies.GE_CART_TOKEN || '';
  let merchantCartToken = '';

  for (let i = 0; i < candidates.length && !geCartToken; i++) {
    const merchantToken = candidates[i];
    const params = {
      MerchantCartToken: merchantToken,
      CountryCode: country,
      CurrencyCode: currency,
      CultureCode: culture,
      MerchantId: merchantId,
      PreferedCultureCode: culture,
      IsJSONP: true,
    };
    const resp = await jsonpGetCartToken(params);
    tokenAttempts.push({
      merchantToken: merchantToken.slice(0, 64),
      success: !!(resp && (resp.CartToken || resp.Success)),
      message: resp && (resp.Message || resp.message) || '',
      hasToken: !!(resp && resp.CartToken),
    });
    if (resp && resp.CartToken) {
      geCartToken = String(resp.CartToken);
      merchantCartToken = merchantToken;
      break;
    }
  }

  // Optional: GEM helper if already loaded on PDP.
  let gemUrl = null;
  try {
    const gem = window.GEM_Components && window.GEM_Components.ExternalMethodsComponent;
    if (gem && typeof gem.GetCheckoutUrl === 'function' && (merchantCartToken || session)) {
      gemUrl = await new Promise((resolve) => {
        let finished = false;
        const finish = (v) => { if (!finished) { finished = true; resolve(v || null); } };
        try { gem.GetCheckoutUrl({ CartToken: merchantCartToken || session }, finish); }
        catch (e) { finish(null); }
        setTimeout(() => finish(null), 4000);
      });
    }
  } catch (e) {}

  done({
    href: String(location.href || ''),
    cookies,
    session,
    sessionUuid,
    country,
    currency,
    culture,
    merchantId,
    candidates: candidates.slice(0, 12),
    summaryAttempts,
    tokenAttempts,
    geCartToken,
    merchantCartToken: merchantCartToken || session || sessionUuid || '',
    gemUrl,
  });
})().catch((e) => done({ error: String(e), cookies: {}, candidates: [], tokenAttempts: [] }));
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

    def discord_content(
        self,
        *,
        area: str,
        product_code: str,
        product_url: str,
        instance: str,
        note: str,
    ) -> str:
        lines = [
            f"**P-Bandai {area} cart success** `{instance}`",
            f"Product: `{product_code}`",
            f"Product URL: {product_url}",
            f"**Checkout (paste in browser):** {self.payment_url}",
        ]
        if self.portable:
            lines.append(
                "_Tokenized Global-e link from held cart (no checkout click). "
                "Paste into a fresh browser to pay._"
            )
        else:
            lines.append(
                "_⚠️ GE cart token not exported yet — use cookies/SESSION below "
                "or retry; do not open CSS/JS links._"
            )
        if self.ge_cart_token:
            lines.append(f"`confirmationCartToken` = `{self.ge_cart_token}`")
        if self.merchant_cart_token:
            lines.append(f"`MerchantCartToken` = `{self.merchant_cart_token}`")
        if self.session_cookie and not self.portable:
            lines.append(f"`SESSION` = `{self.session_cookie}`")
        if note:
            lines.append(f"Note: {note}")
        return "\n".join(lines)[:1900]

    def discord_embed(
        self,
        *,
        area: str,
        product_code: str,
        product_url: str,
        instance: str,
    ) -> Dict[str, Any]:
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
                "value": (self.payment_url or "-")[:1024],
                "inline": False,
            },
        ]
        if self.ge_cart_token:
            fields.append(
                {
                    "name": "confirmationCartToken / GE_CART_TOKEN",
                    "value": f"`{self.ge_cart_token[:300]}`",
                    "inline": False,
                }
            )
        if self.merchant_cart_token:
            fields.append(
                {
                    "name": "MerchantCartToken",
                    "value": f"`{self.merchant_cart_token[:300]}`",
                    "inline": False,
                }
            )
        if self.session_cookie:
            fields.append(
                {
                    "name": "SESSION",
                    "value": f"`{self.session_cookie[:300]}`",
                    "inline": False,
                }
            )
        if self.cookie_header:
            fields.append(
                {
                    "name": "Cookies (Cookie-Editor backup)",
                    "value": f"`{self.cookie_header[:900]}`",
                    "inline": False,
                }
            )
        fields.append(
            {
                "name": "Product",
                "value": (product_url or product_code)[:1024],
                "inline": False,
            }
        )
        return {
            "title": f"Cart OK — {product_code}",
            "description": (
                "Held-cart export (no checkout step). "
                "Paste the checkout link in another browser."
            )[:4096],
            "color": 5763719 if self.portable else 16776960,
            "fields": fields[:25],
        }


def _is_asset_url(url: str) -> bool:
    low = (url or "").lower().split("?", 1)[0]
    if not low.startswith("http"):
        return True
    return any(h in low for h in _ASSET_HINTS)


def _url_has_checkout_token(url: str) -> bool:
    if _is_asset_url(url):
        return False
    low = (url or "").lower()
    if "confirmationcarttoken=" in low:
        return True
    if "carttoken=" in low and ("checkout" in low or "global-e.com" in low):
        return True
    return False


def _cookie_dict_from_driver(driver: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        res = driver.execute_cdp_cmd("Network.getAllCookies", {})
        for c in res.get("cookies") or []:
            name = str(c.get("name") or "")
            if not name:
                continue
            domain = str(c.get("domain") or "").lower()
            if (
                "p-bandai" in domain
                or "global-e" in domain
                or name in _PINNED_COOKIE_NAMES
            ):
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


def _cookie_header(cookies: Dict[str, str]) -> str:
    parts = []
    for name in _PINNED_COOKIE_NAMES:
        val = cookies.get(name)
        if val:
            parts.append(f"{name}={val}")
    for name, val in cookies.items():
        if name in _PINNED_COOKIE_NAMES or not val:
            continue
        if name.lower().startswith("global") or name.upper().startswith("GE_"):
            parts.append(f"{name}={val}")
    return "; ".join(parts)[:1500]


def _build_confirmation_url(
    *,
    base_url: str,
    area_code: str,
    ge_cart_token: str,
    country_code: str = "",
    currency_code: str = "",
) -> str:
    token = (ge_cart_token or "").strip()
    if not token:
        return ""
    area = area_code.strip().lower() or "hk"
    parts = urlparse(f"{base_url.rstrip('/')}/{area}/checkout")
    q: Dict[str, str] = {"confirmationCartToken": token}
    if country_code:
        q["countryCode"] = country_code
    if currency_code:
        q["currencyCode"] = currency_code
    return urlunparse(
        (
            parts.scheme or "https",
            parts.netloc,
            parts.path,
            "",
            urlencode(q),
            "",
        )
    )


def _ids_from_add_to_cart_body(body: str) -> List[str]:
    text = (body or "").strip()
    if not text:
        return []
    found: List[str] = []
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        data = None
    if data is not None:
        stack = [data]
        while stack and len(found) < 20:
            cur = stack.pop()
            if isinstance(cur, dict):
                for k, v in cur.items():
                    if re.search(r"cartid|merchantcarttoken|cart_token|carttoken", str(k), re.I):
                        if v is not None and str(v).strip():
                            found.append(str(v).strip())
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(cur, list):
                stack.extend(cur[:40])
    for m in re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        text,
        flags=re.I,
    ):
        found.append(m)
    # de-dupe
    out: List[str] = []
    for x in found:
        if x not in out:
            out.append(x)
    return out


def export_checkout_from_held_cart(
    driver: Any,
    *,
    base_url: str,
    area_code: str,
    add_to_cart_body: str = "",
) -> PortableCheckout:
    """Export tokenized checkout URL from held cart without leaving the PDP."""
    area = (area_code or "hk").strip().lower()
    merchant_id = _MERCHANT_IDS.get(area, "1925")
    fallback = f"{base_url.rstrip('/')}/{area}/checkout"

    try:
        driver.set_script_timeout(45)
    except Exception:  # noqa: BLE001
        pass

    cookies = _cookie_dict_from_driver(driver)
    snap: Dict[str, Any] = {}
    try:
        snap = driver.execute_async_script(_EXPORT_JS, area, merchant_id) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("held-cart export script failed: %s", exc)
        snap = {"error": str(exc)}

    if not isinstance(snap, dict):
        snap = {}

    js_cookies = snap.get("cookies") if isinstance(snap.get("cookies"), dict) else {}
    merged = {**{str(k): str(v) for k, v in js_cookies.items()}, **cookies}

    ge_token = str(snap.get("geCartToken") or merged.get("GE_CART_TOKEN") or "").strip()
    merchant = str(snap.get("merchantCartToken") or "").strip()
    session = str(snap.get("session") or merged.get("SESSION") or "").strip()
    country = str(snap.get("country") or "").strip().upper() or area.upper()
    currency = str(snap.get("currency") or "").strip().upper()

    # Mine ids from addToCart response body as extra merchant-token candidates
    # if script did not already get a GE token.
    if not ge_token:
        extra_ids = _ids_from_add_to_cart_body(add_to_cart_body)
        if extra_ids:
            logger.info("addToCart body yielded %s id hint(s)", len(extra_ids))

    payment = _build_confirmation_url(
        base_url=base_url,
        area_code=area,
        ge_cart_token=ge_token,
        country_code=country,
        currency_code=currency,
    )
    source = "getCartToken" if payment else "fallback"
    gem_url = str(snap.get("gemUrl") or "").strip()
    if gem_url and not _is_asset_url(gem_url) and _url_has_checkout_token(gem_url):
        payment = gem_url
        source = "gemGetCheckoutUrl"
    if not payment:
        # Last resort: checkout URL is useless alone; still avoid asset URLs.
        payment = fallback
        source = "fallback-no-token"

    portable = bool(ge_token) and _url_has_checkout_token(payment)
    notes: List[str] = []
    if snap.get("error"):
        notes.append(str(snap.get("error")))
    attempts = snap.get("tokenAttempts") or []
    if not portable and attempts:
        notes.append(f"getCartToken attempts={len(attempts)}")

    result = PortableCheckout(
        payment_url=payment,
        portable=portable,
        ge_cart_token=ge_token,
        merchant_cart_token=merchant or session,
        country_code=country,
        currency_code=currency,
        session_cookie=session,
        cookie_header=_cookie_header(merged),
        raw_url=str(snap.get("href") or ""),
        source=source,
        notes=notes,
    )
    logger.info(
        "held-cart export portable=%s source=%s ge_token=%s merchant=%s attempts=%s",
        result.portable,
        result.source,
        "yes" if result.ge_cart_token else "no",
        (result.merchant_cart_token or "")[:48],
        len(attempts) if isinstance(attempts, list) else 0,
    )
    if attempts:
        logger.info("getCartToken attempts detail=%s", json.dumps(attempts)[:800])
    return result


# Back-compat alias used by older call sites.
def extract_portable_checkout(
    driver: Any,
    *,
    base_url: str,
    area_code: str,
    wait_seconds: float = 12.0,
    add_to_cart_body: str = "",
) -> PortableCheckout:
    del wait_seconds  # no polling/navigation loop anymore
    return export_checkout_from_held_cart(
        driver,
        base_url=base_url,
        area_code=area_code,
        add_to_cart_body=add_to_cart_body,
    )
