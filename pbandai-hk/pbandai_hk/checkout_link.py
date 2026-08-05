"""Export a pasteable P-Bandai /orderdetails checkout URL from a held cart.

Bandai / Global-e facts (from live SPA assets):
- ATC holds guest cart in SESSION (login not required for ATC).
- Real checkout route is `/{area}/orderdetails` (NOT `/{area}/checkout`).
- Merchant token: `{cartId}_Checkout_{PRELOAD_DATA.globaleMerchantCartTokenSuffix}`
- Cart UI gates Proceed-to-checkout on login, but the API is:
  `POST /api/cart/{cartSn}/checkout` → `{ checkoutSn, ... }`
- createCheckout body must match SPA: omit shippingAreaCode/defaultAreaCode
  for normal carts; HK eventPickup uses lowercase `hk`|`mo` (not `HK`).
- After createCheckout, `/orderdetails` SSR-injects `PRELOAD_DATA.checkout`.
  Global-e then writes `?confirmationCartToken=<GE_TOKEN>&countryCode=XX`.
- GetCartToken fails until Bandai accepts createCheckout (GE CartNotFound).

Export strategy:
1) Soft-open `/cart` (suffix + DOM `[merchantcarttoken]`)
2) Bootstrap CSRF via `/api/context/member`
3) Read `/api/cart/detail`, build merchant token, try createCheckout variants
4) JSONP Global-e GetCartToken (+ DOM seed)
5) If checkoutSn exists, soft-open `/orderdetails` and poll GE token
6) Return to PDP
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List
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

_JS_HELPERS = r"""
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
    'Accept-Language': 'en',
    'X-Requested-With': 'XMLHttpRequest',
    'X-G1-Area-Code': area,
  }, opts.headers || {});
  const csrf = csrfToken();
  if (csrf) headers['X-CSRF-TOKEN'] = csrf;
  try {
    const init = Object.assign({ credentials: 'include' }, opts);
    init.headers = Object.assign(headers, opts.headers || {});
    const resp = await fetch(url, init);
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

const localize = (cookies) => {
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
  return { country, currency, culture };
};

const pickSubCart = (root) => {
  if (!root || typeof root !== 'object') return null;
  if (Array.isArray(root.subCarts) && root.subCarts.length) {
    // Prefer a subcart that still has line items.
    for (let i = 0; i < root.subCarts.length; i++) {
      const sc = root.subCarts[i];
      if (!sc) continue;
      const n = Number(sc.itemCount || sc.totalItemCount || 0);
      if (n > 0 || (sc.combinedShippings && sc.combinedShippings.length)) return sc;
    }
    return root.subCarts[0];
  }
  if (root.cartId || root.cartSn) return root;
  return null;
};

const lineItemsFromCart = (cartObj) => {
  // SPA: getLineItem().map(li => ({ cartItemSn: li.product.cartItemSn }))
  const lines = [];
  try {
    ((cartObj && cartObj.combinedShippings) || []).forEach((sh) => {
      (sh.lineItems || []).forEach((li) => lines.push(li));
    });
    (cartObj && (cartObj.cartLineItems || cartObj.lineItems || cartObj.items) || []).forEach((li) => lines.push(li));
  } catch (e) {}
  return lines.map((li) => {
    const sn = (li && (
      (li.product && li.product.cartItemSn) || li.cartItemSn || li.cartLineItemSn
    )) || null;
    return sn ? { cartItemSn: sn } : null;
  }).filter(Boolean);
};

const readDefaultShippingAreaCookie = () => {
  try {
    const m = String(document.cookie || '').match(
      /(?:^|;\s*)_BSP_CART_DEFAULT_SHIPPING_AREA_CODE_=([^;]+)/
    );
    return m ? decodeURIComponent(m[1]) : '';
  } catch (e) { return ''; }
};

const seedMerchantToken = (token, cartId) => {
  if (!token) return;
  // Bandai/GE often keep GlobalECartId as bare cartId; DOM holds full merchant token.
  try {
    if (cartId) {
      document.cookie = 'GlobalECartId=' + encodeURIComponent(String(cartId))
        + '; path=/; max-age=3600; SameSite=Lax';
    }
  } catch (e) {}
  try {
    let el = document.getElementById('pbhk-merchant-token');
    if (!el) {
      el = document.createElement('div');
      el.id = 'pbhk-merchant-token';
      el.style.display = 'none';
      (document.body || document.documentElement).appendChild(el);
    }
    el.setAttribute('merchantcarttoken', String(token));
  } catch (e) {}
};

const tryGetCartTokens = async (candidates, country, currency, culture) => {
  const attempts = [];
  let geCartToken = '';
  let merchantCartToken = '';
  const seen = {};
  for (let i = 0; i < candidates.length && !geCartToken; i++) {
    const token = String(candidates[i] || '').trim();
    if (!token || seen[token]) continue;
    seen[token] = true;
    seedMerchantToken(token, token.indexOf('_Checkout_') > 0 ? token.split('_Checkout_')[0] : '');
    const resp = await jsonpGetCartToken({
      MerchantCartToken: token,
      CountryCode: country,
      CurrencyCode: currency,
      CultureCode: culture,
      MerchantId: merchantId,
      PreferedCultureCode: culture,
      IsJSONP: true,
    });
    attempts.push({
      merchantToken: token.slice(0, 120),
      hasToken: !!(resp && resp.CartToken),
      success: !!(resp && (resp.Success || resp.CartToken)),
      message: (resp && (resp.Message || resp.message)) || '',
    });
    if (resp && resp.CartToken) {
      geCartToken = String(resp.CartToken);
      merchantCartToken = token;
      try {
        document.cookie = 'GE_CART_TOKEN=' + encodeURIComponent(geCartToken)
          + '; path=/; max-age=3600; SameSite=Lax';
      } catch (e) {}
      break;
    }
  }
  return { geCartToken, merchantCartToken, attempts };
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
"""

# Primary export: run on /cart after soft navigation.
_EXPORT_CART_JS = _JS_HELPERS + r"""
(async () => {
  const cookies = cookieMap();
  const session = cookies.SESSION || '';
  let sessionUuid = '';
  try { sessionUuid = session ? atob(session) : ''; } catch (e) {}
  const { country, currency, culture } = localize(cookies);
  const steps = [];

  // 1) CSRF bootstrap (cart APIs need X-CSRF-TOKEN).
  const member = await fetchJson('/api/context/member');
  if (member.ok && member.body && member.body.csrfToken) {
    try {
      window.USER_DATA = Object.assign({}, window.USER_DATA || {}, {
        csrfToken: member.body.csrfToken,
      });
    } catch (e) {}
  }
  steps.push({
    step: 'member',
    ok: !!member.ok,
    status: member.status,
    csrf: !!(member.body && member.body.csrfToken) || !!csrfToken(),
  });

  // 2) Suffix from PRELOAD_DATA (present on /cart HTML) or HTML fetch.
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
    if (typeof cartHtml.body === 'string') suffix = readSuffixFromHtml(cartHtml.body);
    steps.push({ step: 'suffixHtml', ok: !!suffix, status: cartHtml.status, suffix: suffix || '' });
  } else {
    steps.push({ step: 'suffixPreload', ok: true, suffix });
  }

  // 3) Wait for Vue cart groups to paint merchantcarttoken.
  let domTokens = [];
  const domDeadline = Date.now() + 10000;
  while (Date.now() < domDeadline && !domTokens.length) {
    try {
      document.querySelectorAll('[merchantcarttoken]').forEach((el) => {
        const v = (el.getAttribute('merchantcarttoken') || '').trim();
        if (v && domTokens.indexOf(v) < 0) domTokens.push(v);
      });
    } catch (e) {}
    if (domTokens.length) break;
    await sleep(250);
  }
  steps.push({
    step: 'domTokens',
    ok: domTokens.length > 0,
    count: domTokens.length,
    tokens: domTokens.slice(0, 3),
  });

  // 4) Cart detail with short retries (ATC may still be committing).
  let detail = { ok: false, status: 0, body: null };
  let summary = { ok: false, status: 0, body: null };
  let cartObj = null;
  for (let attempt = 0; attempt < 5; attempt++) {
    detail = await fetchJson('/api/cart/detail');
    summary = await fetchJson('/api/cart/summary');
    const root = (detail.ok && detail.body) ? detail.body
      : (summary.ok && summary.body) ? summary.body
      : null;
    cartObj = pickSubCart(root);
    const total = root && (root.totalItemCount || root.itemCount) || 0;
    if (cartObj && (cartObj.cartId || cartObj.cartSn || Number(total) > 0)) break;
    await sleep(400 + attempt * 200);
  }
  const root = (detail.ok && detail.body) ? detail.body
    : (summary.ok && summary.body) ? summary.body
    : null;
  cartObj = cartObj || pickSubCart(root);
  const cartId = cartObj && (cartObj.cartId || cartObj.id) || '';
  const cartSn = cartObj && (cartObj.cartSn || cartObj.cartSN) || '';
  const items = lineItemsFromCart(cartObj);
  const hasEventPickup = !!(cartObj && cartObj.eventPickup);
  const defaultShipCookie = readDefaultShippingAreaCookie();
  steps.push({
    step: 'cart',
    detailOk: !!detail.ok,
    detailStatus: detail.status,
    summaryOk: !!summary.ok,
    summaryStatus: summary.status,
    cartId: cartId ? String(cartId).slice(0, 64) : '',
    cartSn: cartSn ? String(cartSn) : '',
    itemCount: items.length,
    totalItemCount: root && root.totalItemCount,
    eventPickup: hasEventPickup,
    defaultShipCookie: defaultShipCookie || '',
  });

  let merchantCartToken = domTokens[0] || '';
  if (!merchantCartToken && cartId && suffix) {
    merchantCartToken = String(cartId) + '_Checkout_' + String(suffix);
  } else if (!merchantCartToken && cartId) {
    merchantCartToken = String(cartId) + '_Checkout_';
  }
  if (merchantCartToken) seedMerchantToken(merchantCartToken, cartId);

  // 5) Bandai createCheckout.
  // SPA payload (Cart-nuJ3): shippingAreaCode / defaultAreaCode are OMITTED for normal
  // carts. HK shipping UI only appears for eventPickup and uses lowercase hk|mo —
  // NOT country "HK". Sending defaultAreaCode:"HK" caused InternalRestApiServerError 500.
  let checkoutSn = '';
  let checkoutStatus = 0;
  let checkoutErr = '';
  let checkoutVariant = '';
  if (cartSn && merchantCartToken) {
    const variants = [];
    // Primary: match non-event-pickup SPA (omit area codes).
    variants.push({ name: 'token+items', body: { merchantCartToken, items } });
    if (hasEventPickup || defaultShipCookie) {
      const ship = defaultShipCookie || 'hk';
      variants.push({
        name: 'ship-' + ship,
        body: { merchantCartToken, shippingAreaCode: ship, items },
      });
      variants.push({
        name: 'ship+default-' + ship,
        body: {
          merchantCartToken,
          shippingAreaCode: ship,
          defaultAreaCode: ship,
          items,
        },
      });
    } else {
      // Fallback lowercase area codes (HK shipping selector values).
      variants.push({
        name: 'ship-hk',
        body: { merchantCartToken, shippingAreaCode: 'hk', items },
      });
      variants.push({
        name: 'ship-hk+default',
        body: {
          merchantCartToken,
          shippingAreaCode: 'hk',
          defaultAreaCode: 'hk',
          items,
        },
      });
    }

    for (let vi = 0; vi < variants.length && !checkoutSn; vi++) {
      const v = variants[vi];
      const created = await fetchJson(
        '/api/cart/' + encodeURIComponent(String(cartSn)) + '/checkout',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(v.body),
        }
      );
      checkoutStatus = created.status;
      checkoutVariant = v.name;
      if (created.ok && created.body && created.body.checkoutSn != null) {
        checkoutSn = String(created.body.checkoutSn);
        try { sessionStorage.setItem('bsp_checkout_sn', checkoutSn); } catch (e) {}
        if (created.body.merchantCartToken) {
          merchantCartToken = String(created.body.merchantCartToken);
          seedMerchantToken(merchantCartToken, cartId);
        }
        checkoutErr = '';
        break;
      }
      try {
        checkoutErr = (typeof created.body === 'object' && created.body)
          ? JSON.stringify(created.body).slice(0, 220)
          : String(created.body || created.error || '').slice(0, 220);
      } catch (e) { checkoutErr = String(created.status || ''); }
      // 401/403 = auth; no point trying more area variants.
      if (created.status === 401 || created.status === 403) break;
    }
  }
  steps.push({
    step: 'createCheckout',
    ok: !!checkoutSn,
    status: checkoutStatus,
    checkoutSn: checkoutSn || '',
    variant: checkoutVariant,
    err: checkoutErr,
    merchantCartToken: merchantCartToken ? String(merchantCartToken).slice(0, 120) : '',
    itemSns: items.map((x) => x.cartItemSn).slice(0, 5),
  });

  // 6) Global-e GetCartToken candidates.
  const candidates = [];
  domTokens.forEach((t) => candidates.push(t));
  if (merchantCartToken) candidates.push(merchantCartToken);
  if (cartId && suffix) candidates.push(String(cartId) + '_Checkout_' + String(suffix));
  if (cartId) candidates.push(String(cartId));
  if (cookies.GlobalECartId) candidates.push(cookies.GlobalECartId);

  let gemUrl = null;
  try {
    const gem = window.GEM_Components && window.GEM_Components.ExternalMethodsComponent;
    if (gem && typeof gem.GetCheckoutUrl === 'function') {
      gemUrl = await new Promise((resolve) => {
        let finished = false;
        const finish = (v) => { if (!finished) { finished = true; resolve(v || null); } };
        try { gem.GetCheckoutUrl({ CartToken: merchantCartToken || candidates[0] || '' }, finish); }
        catch (e) { finish(null); }
        setTimeout(() => finish(null), 4000);
      });
    }
  } catch (e) {}

  const tok = await tryGetCartTokens(candidates, country, currency, culture);
  steps.push({
    step: 'getCartToken',
    ok: !!tok.geCartToken,
    attempts: tok.attempts.length,
    gemUrl: gemUrl || '',
  });

  done({
    href: String(location.href || ''),
    cookies: cookieMap(),
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
    merchantCartToken: tok.merchantCartToken || merchantCartToken,
    geCartToken: tok.geCartToken || cookieMap().GE_CART_TOKEN || '',
    gemUrl,
    steps,
    tokenAttempts: tok.attempts,
    domTokens,
    loginRequiredHint: (!checkoutSn && checkoutStatus === 401) || (!checkoutSn && /login|auth|unauthorized/i.test(checkoutErr)),
  });
})().catch((e) => done({ error: String(e), cookies: cookieMap(), steps: [], tokenAttempts: [] }));
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
    cart_sn: str = ""
    debug: str = ""
    login_required_hint: bool = False
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
                "_Tokenized `/orderdetails` link with `confirmationCartToken`. "
                "Paste into a fresh browser to pay. Do **not** use `/checkout` (404)._"
            )
        else:
            lines.append(
                "_⚠️ GE `confirmationCartToken` missing. "
                "Import SESSION cookies (Cookie-Editor) on `p-bandai.com`, "
                "then open `/orderdetails` (not `/checkout`)._"
            )
            if self.login_required_hint:
                lines.append(
                    "_Bandai `createCheckout` failed for this guest session — "
                    "sign in (same SESSION) → Cart → Proceed to checkout → "
                    "copy the `/orderdetails?confirmationCartToken=…` URL._"
                )
        if self.ge_cart_token:
            lines.append(f"`confirmationCartToken` = `{self.ge_cart_token}`")
        if self.merchant_cart_token:
            lines.append(f"`MerchantCartToken` = `{self.merchant_cart_token}`")
        if self.checkout_sn:
            lines.append(f"`checkoutSn` = `{self.checkout_sn}`")
        if self.cart_id:
            lines.append(f"`cartId` = `{self.cart_id}`")
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
                {"name": "checkoutSn", "value": f"`{self.checkout_sn}`", "inline": True}
            )
        if self.cart_id:
            fields.append(
                {"name": "cartId", "value": f"`{self.cart_id[:80]}`", "inline": True}
            )
        if self.cart_sn:
            fields.append(
                {"name": "cartSn", "value": f"`{self.cart_sn}`", "inline": True}
            )
        if self.session_cookie:
            fields.append(
                {
                    "name": "SESSION (import into browser)",
                    "value": f"`{self.session_cookie[:300]}`",
                    "inline": False,
                }
            )
        if self.cookie_header:
            fields.append(
                {
                    "name": "Cookies (Cookie-Editor)",
                    "value": f"`{self.cookie_header[:900]}`",
                    "inline": False,
                }
            )
        if self.login_required_hint and not self.portable:
            fields.append(
                {
                    "name": "Login note",
                    "value": (
                        "createCheckout failed (guest/area payload). "
                        "GE token needs a checkout hold. "
                        "Import SESSION → sign in → Cart → Proceed → "
                        "`/orderdetails?confirmationCartToken=…`."
                    ),
                    "inline": False,
                }
            )
        if self.debug:
            fields.append(
                {
                    "name": "Export debug",
                    "value": f"`{self.debug[:900]}`",
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
    return "confirmationcarttoken=" in (url or "").lower()


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


def _run_async_js(driver: Any, script: str, area: str, merchant_id: str) -> Dict[str, Any]:
    try:
        driver.set_script_timeout(75)
    except Exception:  # noqa: BLE001
        pass
    try:
        snap = driver.execute_async_script(script, area, merchant_id) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("export script failed: %s", exc)
        return {"error": str(exc), "steps": [], "tokenAttempts": []}
    return snap if isinstance(snap, dict) else {"error": "bad script result"}


def _poll_ge_token_on_orderdetails(
    driver: Any,
    *,
    orderdetails_url: str,
    wait_seconds: float = 16.0,
) -> Dict[str, str]:
    """Open orderdetails after createCheckout and wait for GE token hydration."""
    out = {"ge_cart_token": "", "href": "", "merchant_cart_token": ""}
    try:
        driver.get(orderdetails_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("orderdetails open failed: %s", exc)
        return out
    deadline = time.time() + max(5.0, wait_seconds)
    while time.time() < deadline:
        try:
            href = str(driver.current_url or "")
            out["href"] = href
            # Login redirect — stop early.
            if "/login" in href.lower():
                break
            q = dict(parse_qsl(urlparse(href).query, keep_blank_values=True))
            if q.get("confirmationCartToken"):
                out["ge_cart_token"] = str(q["confirmationCartToken"])
                break
        except Exception:  # noqa: BLE001
            pass
        cookies = _cookie_dict_from_driver(driver)
        if cookies.get("GE_CART_TOKEN"):
            out["ge_cart_token"] = cookies["GE_CART_TOKEN"]
            break
        try:
            dom = driver.execute_script(
                """
                const el = document.querySelector('[merchantcarttoken]');
                let preloadTok = '';
                try {
                  if (window.PRELOAD_DATA && window.PRELOAD_DATA.checkout
                      && window.PRELOAD_DATA.checkout.merchantCartToken) {
                    preloadTok = String(window.PRELOAD_DATA.checkout.merchantCartToken);
                  }
                } catch (e) {}
                return {
                  token: el ? (el.getAttribute('merchantcarttoken') || '') : '',
                  preloadTok,
                  ge: (document.cookie.match(/(?:^|;\\s*)GE_CART_TOKEN=([^;]+)/) || [])[1] || '',
                  hasGlegem: !!window.glegem,
                };
                """
            ) or {}
            if dom.get("token"):
                out["merchant_cart_token"] = str(dom.get("token") or "")
            elif dom.get("preloadTok"):
                out["merchant_cart_token"] = str(dom.get("preloadTok") or "")
            if dom.get("ge"):
                out["ge_cart_token"] = str(dom.get("ge") or "")
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.45)
    return out


def export_checkout_from_held_cart(
    driver: Any,
    *,
    base_url: str,
    area_code: str,
    add_to_cart_body: str = "",
) -> PortableCheckout:
    """Export tokenized /orderdetails URL via cart soft-open + APIs."""
    del add_to_cart_body
    area = (area_code or "hk").strip().lower()
    merchant_id = _MERCHANT_IDS.get(area, "1925")
    fallback = _orderdetails_url(base_url=base_url, area_code=area)

    try:
        pdp_url = str(driver.current_url or "")
    except Exception:  # noqa: BLE001
        pdp_url = f"{base_url.rstrip('/')}/{area}/item/"

    snap: Dict[str, Any] = {"steps": [], "tokenAttempts": []}

    # Phase 1: soft-open /cart (suffix + DOM merchant tokens live here).
    cart_url = f"{base_url.rstrip('/')}/{area}/cart"
    try:
        driver.get(cart_url)
        time.sleep(1.5)
        snap = _run_async_js(driver, _EXPORT_CART_JS, area, merchant_id)
        logger.info(
            "export phase=cart portable_hint=%s checkoutSn=%s cartId=%s steps=%s",
            bool(snap.get("geCartToken")),
            snap.get("checkoutSn") or "-",
            snap.get("cartId") or "-",
            json.dumps(snap.get("steps") or [])[:700],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cart soft-open failed: %s", exc)
        snap = {
            "error": str(exc),
            "steps": [{"step": "cartNav", "ok": False, "error": str(exc)}],
            "tokenAttempts": [],
        }

    # Phase 2: only hydrate orderdetails when Bandai created a checkout hold.
    # Bare /orderdetails without checkout often 500s.
    if not snap.get("geCartToken") and snap.get("checkoutSn"):
        od = _orderdetails_url(
            base_url=base_url,
            area_code=area,
            country_code=str(snap.get("country") or area).upper(),
        )
        polled = _poll_ge_token_on_orderdetails(
            driver, orderdetails_url=od, wait_seconds=16.0
        )
        if polled.get("ge_cart_token"):
            snap["geCartToken"] = polled["ge_cart_token"]
        if polled.get("merchant_cart_token") and not snap.get("merchantCartToken"):
            snap["merchantCartToken"] = polled["merchant_cart_token"]
        steps = list(snap.get("steps") or [])
        steps.append(
            {
                "step": "orderdetailsHydrate",
                "ok": bool(polled.get("ge_cart_token")),
                "href": (polled.get("href") or "")[:180],
            }
        )
        snap["steps"] = steps
        logger.info(
            "export phase=orderdetails ge_token=%s href=%s",
            "yes" if polled.get("ge_cart_token") else "no",
            (polled.get("href") or "")[:160],
        )

    # Always try to return to PDP for continued farming.
    try:
        if pdp_url and ("/item/" in pdp_url or "/item?" in pdp_url):
            driver.get(pdp_url)
        elif pdp_url and "orderdetails" not in pdp_url and "/cart" not in pdp_url:
            driver.get(pdp_url)
    except Exception:  # noqa: BLE001
        pass

    cookies = _cookie_dict_from_driver(driver)
    js_cookies = snap.get("cookies") if isinstance(snap.get("cookies"), dict) else {}
    merged = {**{str(k): str(v) for k, v in js_cookies.items()}, **cookies}

    ge_token = str(snap.get("geCartToken") or merged.get("GE_CART_TOKEN") or "").strip()
    merchant = str(snap.get("merchantCartToken") or "").strip()
    session = str(snap.get("session") or merged.get("SESSION") or "").strip()
    country = str(snap.get("country") or "").strip().upper() or area.upper()
    currency = str(snap.get("currency") or "").strip().upper()
    checkout_sn = str(snap.get("checkoutSn") or "").strip()
    cart_id = str(snap.get("cartId") or "").strip()
    cart_sn = str(snap.get("cartSn") or "").strip()
    login_hint = bool(snap.get("loginRequiredHint"))

    gem_url = str(snap.get("gemUrl") or "").strip()
    payment = _orderdetails_url(
        base_url=base_url,
        area_code=area,
        ge_cart_token=ge_token,
        country_code=country,
        currency_code=currency,
    )
    source = (
        "orderdetails+geToken"
        if ge_token
        else ("orderdetails+checkoutSn" if checkout_sn else "orderdetails-session")
    )
    if gem_url and not _is_asset_url(gem_url) and _url_has_checkout_token(gem_url):
        payment = gem_url
        source = "gemGetCheckoutUrl"

    low = payment.lower().split("?", 1)[0]
    if _is_asset_url(payment) or (
        low.rstrip("/").endswith("/checkout") and "/orderdetails" not in low
    ):
        payment = fallback
        if country:
            payment = _orderdetails_url(
                base_url=base_url, area_code=area, country_code=country
            )
        source = "rejected-bad-url"

    # Keep countryCode on partial links so Cookie-Editor + paste lands in HK.
    if not ge_token and "countrycode=" not in payment.lower():
        payment = _orderdetails_url(
            base_url=base_url, area_code=area, country_code=country
        )

    portable = bool(ge_token) and _url_has_checkout_token(payment)
    if not portable and not checkout_sn:
        for step in snap.get("steps") or []:
            if isinstance(step, dict) and step.get("step") == "createCheckout":
                status = int(step.get("status") or 0)
                err = str(step.get("err") or "").lower()
                if status in (401, 403, 500) or any(
                    x in err for x in ("login", "auth", "unauthorized", "internalrestapi")
                ):
                    login_hint = True
                break

    debug = json.dumps(
        {
            "steps": snap.get("steps") or [],
            "tokenAttempts": (snap.get("tokenAttempts") or [])[:6],
            "error": snap.get("error") or "",
        },
        ensure_ascii=False,
    )[:900]

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
        cart_sn=cart_sn,
        debug=debug,
        login_required_hint=login_hint,
        notes=[],
    )
    logger.info(
        "held-cart export portable=%s source=%s ge_token=%s checkoutSn=%s cartId=%s merchant=%s",
        result.portable,
        result.source,
        "yes" if result.ge_cart_token else "no",
        result.checkout_sn or "-",
        result.cart_id or "-",
        (result.merchant_cart_token or "")[:80],
    )
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
