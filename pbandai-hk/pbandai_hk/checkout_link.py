"""Export a pasteable P-Bandai / Global-e checkout URL from a held cart.

Bandai reality (from storefront JS):
- ATC holds the cart (SESSION).
- Real checkout route is `/{area}/orderdetails` (NOT `/{area}/checkout`).
- Merchant cart token format: `{cartId}_Checkout_{globaleMerchantCartTokenSuffix}`
- Export API: POST `/api/cart/{cartSn}/checkout` → `{ checkoutSn, ... }`
- Global-e then issues GE_CART_TOKEN; pasteable URL uses
  `/{area}/orderdetails?confirmationCartToken=...&countryCode=...`

No Selenium navigation to /cart or /checkout — all via in-page fetch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse, urlunparse

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

# Runs on the current page (PDP). Creates checkout via API, then GE token.
_EXPORT_JS = r"""
const area = String(arguments[0] || 'hk').toLowerCase();
const merchantId = String(arguments[1] || '');
const done = arguments[arguments.length - 1];

const cookieMap = () => {
  const out = {};
  try {
    String(document.cookie || '').split(';').forEach((part) => {
      const i = part.indexOf('=');
      if (i < 0) return;
      const k = part.slice(0, i).trim();
      const v = part.slice(i + 1).trim();
      if (!k) return;
      try { out[k] = decodeURIComponent(v); } catch (e) { out[k] = v; }
    });
  } catch (e) {}
  return out;
};

const csrfToken = () => {
  try {
    if (window.USER_DATA && window.USER_DATA.csrfToken) return String(window.USER_DATA.csrfToken);
  } catch (e) {}
  try {
    const m = document.querySelector('meta[name="csrf-token"]');
    if (m && m.content) return String(m.content);
  } catch (e) {}
  return '';
};

const fetchJson = async (url, opts) => {
  opts = opts || {};
  const headers = Object.assign({
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    'X-G1-Area-Code': area,
  }, opts.headers || {});
  const csrf = csrfToken();
  if (csrf) headers['X-CSRF-TOKEN'] = csrf;
  try {
    const resp = await fetch(url, Object.assign({
      credentials: 'include',
      headers,
    }, opts, { headers }));
    const ct = String(resp.headers.get('content-type') || '');
    let body = null;
    if (ct.indexOf('json') >= 0) {
      try { body = await resp.json(); } catch (e) { body = null; }
    } else {
      try { body = await resp.text(); } catch (e) { body = null; }
    }
    return { ok: resp.ok, status: resp.status, ct, body };
  } catch (e) {
    return { ok: false, status: 0, ct: '', body: null, error: String(e) };
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
  setTimeout(() => finish(null), 9000);
  (document.head || document.documentElement).appendChild(script);
});

const readSuffixFromHtml = (html) => {
  try {
    const m = String(html || '').match(/globaleMerchantCartTokenSuffix"\s*:\s*([0-9]+)/);
    if (m) return m[1];
  } catch (e) {}
  return '';
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

  // Suffix lives in PRELOAD_DATA on cart/checkout pages; fetch cart HTML (no nav).
  let suffix = '';
  try {
    if (window.PRELOAD_DATA && window.PRELOAD_DATA.globaleMerchantCartTokenSuffix != null) {
      suffix = String(window.PRELOAD_DATA.globaleMerchantCartTokenSuffix);
    }
  } catch (e) {}
  if (!suffix) {
    const cartHtml = await fetchJson('/' + area + '/cart', {
      headers: { 'Accept': 'text/html,*/*' },
    });
    if (typeof cartHtml.body === 'string') {
      suffix = readSuffixFromHtml(cartHtml.body);
    }
  }

  // Cart detail shape: { subCarts: [{ cartId, cartSn, combinedShippings: [{ lineItems }] }] }
  const detail = await fetchJson('/api/cart/detail');
  const summary = detail.ok ? null : await fetchJson('/api/cart/summary');
  const root = (detail.ok && detail.body) ? detail.body
    : (summary && summary.ok && summary.body) ? summary.body
    : null;
  const subCarts = (root && Array.isArray(root.subCarts)) ? root.subCarts : [];
  const cartObj = subCarts[0] || root || null;

  const cartId = cartObj && (cartObj.cartId || cartObj.id) || '';
  const cartSn = cartObj && (cartObj.cartSn || cartObj.cartSN) || '';
  const shippingArea = (cartObj && (cartObj.shippingAreaCode || cartObj.areaCode
    || (cartObj.deliveryGroup && cartObj.deliveryGroup.areaCode))) || country;
  const defaultArea = area.toUpperCase();

  // Build Bandai merchant token: {cartId}_Checkout_{suffix}
  let merchantCartToken = '';
  if (cartId && suffix) {
    merchantCartToken = String(cartId) + '_Checkout_' + String(suffix);
  } else if (cartId) {
    merchantCartToken = String(cartId) + '_Checkout_';
  }

  // Line items for proceed-to-checkout API (from combinedShippings.lineItems).
  let items = [];
  try {
    const lines = [];
    const ships = (cartObj && cartObj.combinedShippings) || [];
    if (Array.isArray(ships)) {
      ships.forEach((sh) => {
        (sh.lineItems || []).forEach((li) => lines.push(li));
      });
    }
    (cartObj && (cartObj.cartLineItems || cartObj.lineItems || cartObj.items) || []).forEach((li) => lines.push(li));
    items = lines.map((li) => {
      const sn = (li && (li.cartItemSn || (li.product && li.product.cartItemSn) || li.cartLineItemSn)) || null;
      return sn ? { cartItemSn: sn } : null;
    }).filter(Boolean);
  } catch (e) {}

  const steps = [];
  steps.push({
    step: 'cartDetail',
    ok: !!(detail && detail.ok),
    status: detail ? detail.status : 0,
    cartId: cartId ? String(cartId).slice(0, 64) : '',
    cartSn: cartSn ? String(cartSn) : '',
    suffix: suffix || '',
    itemCount: items.length,
  });

  // Export checkout hold → checkoutSn (Bandai's "proceed to checkout" API).
  let checkoutSn = '';
  let checkoutResp = null;
  if (cartSn && merchantCartToken) {
    const body = {
      merchantCartToken: merchantCartToken,
      shippingAreaCode: shippingArea || country,
      defaultAreaCode: defaultArea,
      items: items,
    };
    const created = await fetchJson('/api/cart/' + encodeURIComponent(String(cartSn)) + '/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    checkoutResp = {
      ok: !!created.ok,
      status: created.status,
      error: created.error || '',
      bodyKeys: created.body && typeof created.body === 'object' ? Object.keys(created.body).slice(0, 20) : [],
    };
    if (created.ok && created.body && created.body.checkoutSn != null) {
      checkoutSn = String(created.body.checkoutSn);
      try { sessionStorage.setItem('bsp_checkout_sn', checkoutSn); } catch (e) {}
      // Prefer merchant token returned by API if present.
      if (created.body.merchantCartToken) {
        merchantCartToken = String(created.body.merchantCartToken);
      }
    }
    steps.push({ step: 'createCheckout', ok: !!checkoutSn, status: created.status, checkoutSn: checkoutSn });
  } else {
    steps.push({
      step: 'createCheckout',
      ok: false,
      status: 0,
      reason: !cartSn ? 'missing cartSn' : 'missing merchantCartToken',
    });
  }

  // Ask Global-e for portable cart token using the REAL merchant token format.
  let geCartToken = cookies.GE_CART_TOKEN || '';
  const tokenAttempts = [];
  const candidates = [];
  if (merchantCartToken) candidates.push(merchantCartToken);
  if (cartId && suffix) candidates.push(String(cartId) + '_Checkout_' + String(suffix));
  if (cartId) candidates.push(String(cartId));
  if (sessionUuid) candidates.push(sessionUuid);
  if (session) candidates.push(session);

  for (let i = 0; i < candidates.length && !geCartToken; i++) {
    const token = candidates[i];
    const resp = await jsonpGetCartToken({
      MerchantCartToken: token,
      CountryCode: country,
      CurrencyCode: currency,
      CultureCode: culture,
      MerchantId: merchantId,
      PreferedCultureCode: culture,
      IsJSONP: true,
    });
    tokenAttempts.push({
      merchantToken: String(token).slice(0, 96),
      hasToken: !!(resp && resp.CartToken),
      success: !!(resp && (resp.Success || resp.CartToken)),
      message: resp && (resp.Message || resp.message) || '',
    });
    if (resp && resp.CartToken) {
      geCartToken = String(resp.CartToken);
      merchantCartToken = token;
      break;
    }
  }
  steps.push({ step: 'getCartToken', ok: !!geCartToken, attempts: tokenAttempts.length });

  done({
    href: String(location.href || ''),
    cookies,
    session,
    sessionUuid,
    country,
    currency,
    culture,
    merchantId,
    suffix,
    cartId: cartId ? String(cartId) : '',
    cartSn: cartSn ? String(cartSn) : '',
    checkoutSn,
    merchantCartToken,
    geCartToken,
    checkoutResp,
    steps,
    tokenAttempts,
  });
})().catch((e) => done({
  error: String(e),
  cookies: {},
  steps: [],
  tokenAttempts: [],
}));
"""


@dataclass
class PortableCheckout:
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
    checkout_sn: str = ""
    cart_id: str = ""
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
                "_Tokenized `/orderdetails` link (held-cart export). "
                "Paste into a fresh browser to pay._"
            )
        else:
            lines.append(
                "_⚠️ GE token missing — import SESSION cookies then open `/orderdetails`. "
                "Do not use `/checkout` (that route 404s)._"
            )
        if self.ge_cart_token:
            lines.append(f"`confirmationCartToken` = `{self.ge_cart_token}`")
        if self.merchant_cart_token:
            lines.append(f"`MerchantCartToken` = `{self.merchant_cart_token}`")
        if self.checkout_sn:
            lines.append(f"`checkoutSn` = `{self.checkout_sn}`")
        if self.session_cookie:
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
                    "name": "confirmationCartToken",
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
        if self.checkout_sn:
            fields.append(
                {
                    "name": "checkoutSn",
                    "value": f"`{self.checkout_sn}`",
                    "inline": True,
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
                "Held-cart export → `/orderdetails` "
                "(Bandai checkout route; `/checkout` is invalid)."
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
    return "confirmationcarttoken=" in low


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


def _orderdetails_url(
    *,
    base_url: str,
    area_code: str,
    ge_cart_token: str = "",
    country_code: str = "",
    currency_code: str = "",
) -> str:
    area = area_code.strip().lower() or "hk"
    parts = urlparse(f"{base_url.rstrip('/')}/{area}/orderdetails")
    q: Dict[str, str] = {}
    if ge_cart_token:
        q["confirmationCartToken"] = ge_cart_token
    if country_code:
        q["countryCode"] = country_code
    if currency_code and ge_cart_token:
        q["currencyCode"] = currency_code
    return urlunparse(
        (
            parts.scheme or "https",
            parts.netloc,
            parts.path,
            "",
            urlencode(q) if q else "",
            "",
        )
    )


def export_checkout_from_held_cart(
    driver: Any,
    *,
    base_url: str,
    area_code: str,
    add_to_cart_body: str = "",
) -> PortableCheckout:
    """Export tokenized /orderdetails checkout URL from held cart (no UI checkout)."""
    del add_to_cart_body  # reserved for future body mining
    area = (area_code or "hk").strip().lower()
    merchant_id = _MERCHANT_IDS.get(area, "1925")
    fallback = _orderdetails_url(base_url=base_url, area_code=area)

    try:
        driver.set_script_timeout(60)
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
    checkout_sn = str(snap.get("checkoutSn") or "").strip()
    cart_id = str(snap.get("cartId") or "").strip()

    payment = _orderdetails_url(
        base_url=base_url,
        area_code=area,
        ge_cart_token=ge_token,
        country_code=country,
        currency_code=currency,
    )
    # Never emit /checkout (invalid SPA route → "wandered off").
    if "/checkout" in payment.lower() and "/orderdetails" not in payment.lower():
        payment = fallback

    if _is_asset_url(payment):
        payment = fallback
        ge_token = ""

    portable = bool(ge_token) and _url_has_checkout_token(payment)
    source = "orderdetails+geToken" if portable else (
        "orderdetails+checkoutSn" if checkout_sn else "orderdetails-fallback"
    )

    notes: List[str] = []
    if snap.get("error"):
        notes.append(str(snap.get("error")))
    steps = snap.get("steps") or []
    attempts = snap.get("tokenAttempts") or []
    if not portable:
        notes.append("ge_token_missing")
    if checkout_sn:
        notes.append(f"checkoutSn={checkout_sn}")

    result = PortableCheckout(
        payment_url=payment,
        portable=portable,
        ge_cart_token=ge_token,
        merchant_cart_token=merchant,
        country_code=country,
        currency_code=currency,
        session_cookie=session,
        cookie_header=_cookie_header(merged),
        raw_url=str(snap.get("href") or ""),
        source=source,
        checkout_sn=checkout_sn,
        cart_id=cart_id,
        notes=notes,
    )
    logger.info(
        "held-cart export portable=%s source=%s ge_token=%s checkoutSn=%s merchant=%s steps=%s",
        result.portable,
        result.source,
        "yes" if result.ge_cart_token else "no",
        result.checkout_sn or "-",
        (result.merchant_cart_token or "")[:80],
        json.dumps(steps)[:500],
    )
    if attempts:
        logger.info("getCartToken attempts=%s", json.dumps(attempts)[:800])
    return result


def extract_portable_checkout(
    driver: Any,
    *,
    base_url: str,
    area_code: str,
    wait_seconds: float = 12.0,
    add_to_cart_body: str = "",
) -> PortableCheckout:
    del wait_seconds
    return export_checkout_from_held_cart(
        driver,
        base_url=base_url,
        area_code=area_code,
        add_to_cart_body=add_to_cart_body,
    )
