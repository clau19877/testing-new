# P-Bandai HK module

Hong Kong storefront module for [Premium Bandai HK](https://p-bandai.com/hk), adapted from the flow of [`prebanClawler2`](https://github.com/jsakamoto65535/prebanClawler2).

JP and HK are different platforms. This module does **not** scrape JP DOM (`#cdu2mainColumn`, `#buy_side`). It uses the HK JSON APIs behind the Vue app.

## What it does

- Watch **direct product links/codes** you already know
- Optional keyword poll via `GET /api/search` with `X-G1-Area-Code: hk`
- Filter sale status with `_f_productStatuses` (same facet encoding as the HK site UI)
- Match product names (`en` / `zh-HK`) against keyword lists
- File + console logging (`logs/pbandai_hk.log`), including error tracebacks
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
# check one direct product URL/code
python web_shopping_bot_hk.py check "https://p-bandai.com/hk/item/A2742450001"

# one-shot scan (direct links and/or keyword lists from .env)
python web_shopping_bot_hk.py once

# ad-hoc API search
python web_shopping_bot_hk.py search "HG"

# continuous loop / schedule from .env
python web_shopping_bot_hk.py loop
```

### Direct-link `.env` example

```env
PRODUCT_LINKS=https://p-bandai.com/hk/item/A2742450001,https://p-bandai.com/hk/item/A2690472004
SEARCH_KEYWORDS=
SALE_STATUSES=On,Waiting
ENABLE_ADD_TO_CART=0
LOG_FILE=logs/pbandai_hk.log
```

Then:

```bash
python web_shopping_bot_hk.py once
# or keep watching
python web_shopping_bot_hk.py loop
```

Errors/tracebacks are appended to `logs/pbandai_hk.log`.

## Important `.env` knobs

| Key | Purpose |
|---|---|
| `PRODUCT_LINKS` | Direct HK item URLs (comma-separated) |
| `PRODUCT_CODES` | Bare product codes (comma-separated) |
| `SEARCH_KEYWORDS` | Optional keywords for `/api/search` |
| `PRECHECK_LIST` | Coarse name filter (search mode) |
| `TARGET_LIST` | Finer name filter (defaults to `PRECHECK_LIST` if empty) |
| `SALE_STATUSES` | Usually `On,Waiting` |
| `ENABLE_ADD_TO_CART` | `0` monitor only, `1` cart mode |
| `SCHEDULE_MODE` | `0` immediate loop, `1` clock schedule |
| `LOG_FILE` / `LOG_LEVEL` | Error/info logging path and level |
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
  logs/                    # created at runtime
  pbandai_hk/
    api.py                 # HK API client
    bot.py                 # scan / match / optional cart loop
    config.py
    links.py               # parse direct product URLs/codes
    logging_utils.py       # file/console error logging
    notify.py
    session_login.py       # Selenium cookie transfer
```
