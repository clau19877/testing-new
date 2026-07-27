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

## One-click setup & launch

### Windows
1. Install [Python 3.10+](https://www.python.org/downloads/) (enable **Add python.exe to PATH**)
2. Double-click either:
   - `dist/PBandaiHK.exe` — menu after setup
   - `OneClickMonitor.bat` — setup + start monitor loop
   - `SetupAndLaunch.bat` — same as the exe menu

First run creates `.venv`, installs deps, and copies `.env` for you.

If you already have an old `.env`, either:
- Launcher menu **`[0] Reset .env from latest .env.example`** (backs up old file), or
- Copy `pbandai-hk/.env.example` → `pbandai-hk/.env` yourself  
The Windows zip now includes both `.env` and `.env.example` (drop-ready for `A2891018001`).

### macOS / Linux
```bash
./setup_and_launch.sh
# or
./dist/PBandaiHK
```

Rebuild launchers anytime with `./build_launcher.sh`.

## Manual setup

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
RETRY_WAIT=60
```

Then:

```bash
python web_shopping_bot_hk.py once
# or keep watching
python web_shopping_bot_hk.py loop
```

Errors/tracebacks are appended to `logs/pbandai_hk.log`.

## Multi-sessions + proxy

### Single proxy (simple)
```env
PROXY_URL=http://user:pass@1.2.3.4:8080
# or socks5://1.2.3.4:1080
```

### CSV parallel tasks (recommended for many accounts)
1. Copy examples and fill real values:
```bash
cp task.example.csv task.csv
cp proxy.example.csv proxy.csv
```
2. `task.csv` columns: `name,login,password`  
   `proxy.csv` column: `proxy` — one per line. Accepted formats:
   - `host:port:user:pass` (common provider format)
   - `http://user:pass@host:port`
   - `socks5://host:1080`
   - or columns `host,port,username,password`
3. Parallel count = number of rows in `task.csv`. Each task picks one proxy at random.
```bash
python web_shopping_bot_hk.py tasks
# or start once/loop — if task.csv exists it auto-logins all tasks first
python web_shopping_bot_hk.py loop
```
4. Tip: set `BACKGROUND_MODE=1` for headless parallel browsers.

### Multi account sessions (manual)
1. Create/login sessions (one browser login each):
```bash
python web_shopping_bot_hk.py login --name acc1 --proxy http://user:pass@1.2.3.4:8080
python web_shopping_bot_hk.py login --name acc2 --proxy socks5://5.6.7.8:1080
python web_shopping_bot_hk.py sessions
```
2. This writes `sessions.json` + `sessions/*.cookies.json`
3. Set cart fan-out mode:
```env
SESSIONS_FILE=sessions.json
CART_MODE=first          # stop after first success
# CART_MODE=all          # try every session
# CART_MODE=round_robin  # rotate starting session
ENABLE_ADD_TO_CART=1
```

Launcher menu also has:
- `[6] List sessions`
- `[7] Login / create session (multi + proxy)`
- `[8] Login all task.csv (parallel + random proxies)`

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
| `PROXY_URL` | Single/fallback proxy |
| `SESSIONS_FILE` | Multi-session config (`sessions.json`) |
| `CART_MODE` | `first` / `all` / `round_robin` |
| `CART_METHOD` | `auto` / `api` / `browser` / `warm` |
| `PREWARM_BROWSERS` | `1` = park Chrome on PDP before drop (use with `auto`/`warm`) |
| `TASK_CSV` | Accounts file (`name,login,password`) |
| `PROXY_CSV` | Proxy pool; each task picks one at random |
| `PROXY_ASSIGN_MODE` | `random` or `unique` |
| `TASK_PARALLEL_WORKERS` | `0` = one worker per task |
| `EMAIL_USER` | Leave empty to skip email |

## Drop / site-crash strategy (warm browsers)

During drops the product **HTML** often 502/503 while `/api/products` and cart still work. Cold-opening Chrome at T-0 usually fails.

### Target example: `A2891018001` (GUNDAM CARD GAME 1ST ANNIVERSARY SET)
API snapshot before open:
- `preOrderStatus=NotStarted` until `orderStartDate=2026-07-27T08:00:00Z`
- `availabilityStatus=Waiting`, `purchaseAvailable=false` (soft)
- `availableQty=2`, `maxQuantity=2` / max per user 2
- `areaItemNo=AAI0014136HK`
- Bot now **waits** until order opens (does not cart while `NotStarted`)

Recommended `.env` for this drop:

```env
ENABLE_ADD_TO_CART=1
PRODUCT_LINKS=https://p-bandai.com/hk/item/A2891018001
CART_METHOD=warm
PREWARM_BROWSERS=1
BACKGROUND_MODE=0
CART_MODE=all
CART_PARALLEL=1
CART_QTY=1
REQUIRE_CART_INCREASE=1
DROP_LEAD_SECONDS=30
RETRY_WAIT=60
SALE_STATUSES=On,Waiting
```

Flow:
1. Login all sessions (task.csv / menu `[8]`) **before** the drop
2. Start `loop` early — browsers park on PDP (or HK home if PDP is down)
3. Bot waits on `preOrderStatus` / `orderStartDate` (sleeps until ~T-30s, then fast-polls)
4. When open, cart fires via **in-page `fetch('/api/cart/addToCart')`** (no HTML reload)
5. Success requires cart count increase (`REQUIRE_CART_INCREASE=1`) — no false “clicked / verify on site” exits

Do **not** use cold `CART_METHOD=browser` alone for drops — that relaunches Chrome and reloads HTML under load.

## Cart mode notes

When `ENABLE_ADD_TO_CART=1`:

1. Chrome/Edge opens `LOGIN_URL`
2. You log in manually
3. Cookies + CSRF are copied into the API client
4. Matching purchasable items are posted to `/api/cart/addToCart` as:

```json
[{ "areaItemNo": "AAI........HK", "qty": 1 }]
```

If Windows shows `WinError 193` during login:
- Install/update **Google Chrome** or **Microsoft Edge**
- Delete the broken driver cache folder: `%USERPROFILE%\.wdm`
- Set `BROWSER=edge` in `.env` and retry
- Or export cookies to a file and set `COOKIE_FILE=...` to skip Selenium

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
  SetupAndLaunch.bat       # Windows one-click menu
  OneClickMonitor.bat      # Windows one-click monitor loop
  setup_and_launch.sh      # macOS/Linux one-click
  launch.py                # Python launcher fallback
  build_launcher.sh        # rebuild dist/PBandaiHK(.exe)
  dist/PBandaiHK.exe       # Windows launcher
  dist/PBandaiHK           # Linux launcher
  web_shopping_bot_hk.py   # CLI entry
  requirements.txt
  .env.example
  task.example.csv         # copy to task.csv (login/password accounts)
  proxy.example.csv        # copy to proxy.csv (proxy pool)
  logs/                    # created at runtime
  launcher/main.go         # launcher source
  pbandai_hk/
    api.py                 # HK API client
    bot.py                 # scan / match / optional cart loop
    config.py
    csv_tasks.py           # task.csv / proxy.csv loaders
    task_runner.py         # parallel CSV logins
    links.py               # parse direct product URLs/codes
    logging_utils.py       # file/console error logging
    notify.py
    session_login.py       # Selenium cookie transfer
    browser_cart.py        # cold browser add-to-cart
    warm_cart.py           # pre-warmed browsers for drops
    diagnostics.py         # cart eligibility diagnose
```
