# P-Bandai HK module

Hong Kong storefront module for [Premium Bandai HK](https://p-bandai.com/hk), adapted from the flow of [`prebanClawler2`](https://github.com/jsakamoto65535/prebanClawler2).

JP and HK are different platforms. This module does **not** scrape JP DOM (`#cdu2mainColumn`, `#buy_side`). It uses the HK JSON APIs behind the Vue app.

## What it does

- Poll HK catalog via `GET /api/search` with `X-G1-Area-Code: hk`
- Filter sale status with `_f_productStatuses` (same facet encoding as the HK site UI)
- Match product names (`en` / `zh-HK`) against keyword lists
- Optional email notify
- Optional add-to-cart via `POST /api/cart/addToCart` after manual browser login
- Immediate loop or scheduled runs (same idea as the JP bot)

Default mode is **monitor + notify only** (`ENABLE_ADD_TO_CART=0`).

## Setup

```bash
cd pbandai-hk
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
```

## Quick use

```bash
# one-shot scan using PRECHECK/TARGET lists
python web_shopping_bot_hk.py once

# ad-hoc API search
python web_shopping_bot_hk.py search "HG"

# continuous loop / schedule from .env
python web_shopping_bot_hk.py loop
```

## Important `.env` knobs

| Key | Purpose |
|---|---|
| `SEARCH_KEYWORDS` | Keywords used to query `/api/search` |
| `PRECHECK_LIST` | Coarse name filter |
| `TARGET_LIST` | Finer name filter (defaults to `PRECHECK_LIST` if empty) |
| `SALE_STATUSES` | Usually `On,Waiting` |
| `ENABLE_ADD_TO_CART` | `0` monitor only, `1` cart mode |
| `SCHEDULE_MODE` | `0` immediate loop, `1` clock schedule |
| `EMAIL_USER` | Leave empty to skip email |

## Cart mode notes

When `ENABLE_ADD_TO_CART=1`:

1. Chrome opens `LOGIN_URL`
2. You log in manually
3. Cookies + CSRF are copied into the API client
4. Matching purchasable items are posted to `/api/cart/addToCart` as:

```json
[{ "areaItemNo": "AAI........HK", "qty": 1 }]
```

Cart/checkout still follows P-Bandai / Global-e site rules. Use only with your own account and for personal purchasing.

## Why a new module was required

| JP bot assumption | HK reality |
|---|---|
| `p-bandai.jp/new_itemlist/` | No equivalent static list page |
| XPath `#cdu2mainColumn` / `#buy_side` | Vue SPA + `/api/*` |
| JP stock text `在庫がありません` | `saleStatus` / `purchaseAvailable` / cart error codes |
| Same-site cart click | REST cart + CSRF (`/api/context/member`) |

## Layout

```
pbandai-hk/
  web_shopping_bot_hk.py   # CLI entry
  requirements.txt
  .env.example
  pbandai_hk/
    api.py                 # HK API client
    bot.py                 # scan / match / optional cart loop
    config.py
    notify.py
    session_login.py       # Selenium cookie transfer
```
