"""Capture a held guest cart and hand it off for manual login + checkout.

Why no automated checkout export:
Bandai only creates the checkout hold (`POST /api/cart/{cartSn}/checkout`) for
signed-in members. Guests get `500 InternalRestApiServerError` for every payload
shape, so Global-e never issues a `confirmationCartToken` and no pasteable
checkout URL can exist for a guest cart.

So after ATC the bot stops clicking on that instance, parks its Chrome window on
`/{area}/cart`, and notifies Discord. You log in in that window (or import the
cookies elsewhere) and press Proceed to checkout yourself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .logging_utils import get_logger

logger = get_logger("checkout_link")

_PINNED_COOKIE_NAMES = (
    "SESSION",
    "GlobalECartId",
    "GlobalE_Data",
    "GE_CART_TOKEN",
    "JSESSIONID",
)

# Read cart identity without leaving the current page.
_CART_SNAPSHOT_JS = r"""
const area = String(arguments[0] || 'hk').toLowerCase();
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
  return '';
};

const fetchJson = async (url) => {
  const headers = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en',
    'X-Requested-With': 'XMLHttpRequest',
    'X-G1-Area-Code': area,
  };
  const csrf = csrfToken();
  if (csrf) headers['X-CSRF-TOKEN'] = csrf;
  try {
    const resp = await fetch(url, { credentials: 'include', headers });
    let body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    return { ok: resp.ok, status: resp.status, body };
  } catch (e) {
    return { ok: false, status: 0, body: null };
  }
};

const pickSubCart = (root) => {
  if (!root || typeof root !== 'object') return null;
  if (Array.isArray(root.subCarts) && root.subCarts.length) {
    for (let i = 0; i < root.subCarts.length; i++) {
      const sc = root.subCarts[i];
      if (sc && (Number(sc.itemCount || sc.totalItemCount || 0) > 0
          || (sc.combinedShippings && sc.combinedShippings.length))) return sc;
    }
    return root.subCarts[0];
  }
  return (root.cartId || root.cartSn) ? root : null;
};

(async () => {
  const cookies = cookieMap();
  const member = await fetchJson('/api/context/member');
  const loggedIn = !!(member.body && member.body.loggedInMember
    && (member.body.loggedInMember.memberNo || member.body.loggedInMember.memberId));

  // Retry briefly: ATC may still be committing when we read the cart.
  let detail = { ok: false, status: 0, body: null };
  let cartObj = null;
  let itemCount = 0;
  for (let attempt = 0; attempt < 4; attempt++) {
    detail = await fetchJson('/api/cart/detail');
    const root = (detail.ok && detail.body) ? detail.body : null;
    cartObj = pickSubCart(root);
    itemCount = Number((root && (root.totalItemCount || root.itemCount)) || 0);
    if (cartObj || itemCount > 0) break;
    await new Promise((r) => setTimeout(r, 400 + attempt * 200));
  }

  done({
    cookies,
    loggedIn,
    cartId: (cartObj && (cartObj.cartId || cartObj.id)) || '',
    cartSn: (cartObj && (cartObj.cartSn || cartObj.cartSN)) || '',
    itemCount,
    detailStatus: detail.status,
    href: String(location.href || ''),
  });
})().catch((e) => done({ error: String(e), cookies: cookieMap() }));
"""


@dataclass
class CartHandoff:
    """A held cart or button-live handoff, ready for manual checkout."""

    cart_url: str
    cart_id: str = ""
    cart_sn: str = ""
    item_count: int = 0
    logged_in: bool = False
    session_cookie: str = ""
    cookie_header: str = ""
    parked: bool = False
    # "cart" = ATC succeeded; "button_live" = PLACE PRE-ORDER visible, no auto-click.
    kind: str = "cart"
    debug: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def is_button_live(self) -> bool:
        return self.kind == "button_live" or "button_live" in self.notes

    def discord_content(
        self,
        *,
        area: str,
        product_code: str,
        product_url: str,
        instance: str,
        note: str,
    ) -> str:
        if self.is_button_live:
            lines = [
                f"🟢 **P-Bandai {area} PRE-ORDER BUTTON LIVE** — `{instance}`",
                f"Product: `{product_code}`",
                f"Product URL: {product_url}",
                "",
                "**Bot stopped** — no more refreshing or clicking.",
                "Chrome windows are still open on the product page.",
                "**Click PLACE PRE-ORDER yourself**, then log in and check out.",
            ]
            if note:
                lines.append(f"Note: {note}")
            return "\n".join(lines)[:1900]
        lines = [
            f"🛒 **P-Bandai {area} CART SECURED** — `{instance}`",
            f"Product: `{product_code}`",
            f"Product URL: {product_url}",
            "",
            f"**This instance has stopped clicking.** Its Chrome window is "
            f"{'parked on the cart page' if self.parked else 'still open'} — "
            "**log in there and check out manually.**",
            f"Cart page: {self.cart_url}",
        ]
        if not self.logged_in:
            lines.append(
                "_Guest session: sign in **in that same window** so the cart carries over. "
                "To finish on another machine, import the cookies below first._"
            )
        if self.session_cookie:
            lines.append(f"`SESSION` = `{self.session_cookie}`")
        if self.cart_id:
            lines.append(f"`cartId` = `{self.cart_id}`")
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
        if self.is_button_live:
            return {
                "title": f"Pre-order button live — {product_code}",
                "description": (
                    "PLACE PRE-ORDER is on the page. The bot has stopped refreshing. "
                    "Switch to a Chrome window, click the button, then check out manually."
                )[:4096],
                "color": 5763719,
                "fields": [
                    {
                        "name": "Instance",
                        "value": f"`{instance}` (stopped)",
                        "inline": True,
                    },
                    {"name": "Area", "value": f"`{area}`", "inline": True},
                    {
                        "name": "Next step",
                        "value": (
                            "In an open Chrome window on the product page, click "
                            "**PLACE PRE-ORDER**, sign in if needed, then "
                            "**Proceed to checkout**."
                        )[:1024],
                        "inline": False,
                    },
                    {
                        "name": "Product",
                        "value": (product_url or product_code)[:1024],
                        "inline": False,
                    },
                ][:25],
            }
        fields = [
            {"name": "Instance", "value": f"`{instance}` (stopped)", "inline": True},
            {"name": "Area", "value": f"`{area}`", "inline": True},
            {
                "name": "Signed in",
                "value": "yes ✅" if self.logged_in else "guest ⚠️",
                "inline": True,
            },
            {"name": "Cart page", "value": self.cart_url[:1024], "inline": False},
            {
                "name": "Next step",
                "value": (
                    "Switch to this instance's Chrome window"
                    + (" (already on the cart page)" if self.parked else "")
                    + ", sign in, then press **Proceed to checkout**.\n"
                    "Finishing elsewhere? Import the cookies below with "
                    "Cookie-Editor on `p-bandai.com` first."
                )[:1024],
                "inline": False,
            },
        ]
        if self.item_count:
            fields.append(
                {"name": "Items in cart", "value": str(self.item_count), "inline": True}
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
        fields.append(
            {
                "name": "Product",
                "value": (product_url or product_code)[:1024],
                "inline": False,
            }
        )
        return {
            "title": f"Cart secured — {product_code}",
            "description": (
                "Add-to-cart succeeded and the cart is held on this session. "
                "Log in and complete checkout manually."
            )[:4096],
            "color": 5763719,
            "fields": fields[:25],
        }


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


def capture_cart_handoff(
    driver: Any,
    *,
    base_url: str,
    area_code: str,
    park_on_cart: bool = True,
) -> CartHandoff:
    """Snapshot the held cart, then optionally park the window on /{area}/cart."""
    area = (area_code or "hk").strip().lower()
    cart_url = f"{base_url.rstrip('/')}/{area}/cart"

    snap: Dict[str, Any] = {}
    try:
        driver.set_script_timeout(30)
    except Exception:  # noqa: BLE001
        pass
    try:
        snap = driver.execute_async_script(_CART_SNAPSHOT_JS, area) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("cart snapshot failed: %s", exc)
        snap = {"error": str(exc)}
    if not isinstance(snap, dict):
        snap = {}

    cookies = _cookie_dict_from_driver(driver)
    js_cookies = snap.get("cookies") if isinstance(snap.get("cookies"), dict) else {}
    merged = {**{str(k): str(v) for k, v in js_cookies.items()}, **cookies}

    parked = False
    if park_on_cart:
        try:
            driver.get(cart_url)
            parked = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("park on cart failed: %s", exc)

    result = CartHandoff(
        cart_url=cart_url,
        cart_id=str(snap.get("cartId") or "").strip(),
        cart_sn=str(snap.get("cartSn") or "").strip(),
        item_count=int(snap.get("itemCount") or 0),
        logged_in=bool(snap.get("loggedIn")),
        session_cookie=str(merged.get("SESSION") or "").strip(),
        cookie_header=_cookie_header(merged),
        parked=parked,
        debug=json.dumps(
            {
                "detailStatus": snap.get("detailStatus"),
                "href": str(snap.get("href") or "")[:160],
                "error": snap.get("error") or "",
            },
            ensure_ascii=False,
        )[:400],
    )
    logger.info(
        "cart handoff cartId=%s cartSn=%s items=%s loggedIn=%s parked=%s",
        result.cart_id or "-",
        result.cart_sn or "-",
        result.item_count,
        result.logged_in,
        result.parked,
    )
    return result
