# P-Bandai HK module

Hong Kong storefront module for [Premium Bandai HK](https://p-bandai.com/hk), adapted from the flow of [`prebanClawler2`](https://github.com/jsakamoto65535/prebanClawler2).

JP and HK are different platforms. This module does **not** scrape JP DOM (`#cdu2mainColumn`, `#buy_side`). It uses the HK JSON APIs behind the Vue app.

## What it does

- **Click farm (default):** open N guest Chrome windows, watch for **PLACE PRE-ORDER**, then **stop** and leave the browsers open so you can ATC + checkout manually
- Optional `AUTO_ATC=1` to auto-click the button instead
- Optional monitor-only mode (`ENABLE_ADD_TO_CART=0`) for stock watching
- File + console logging (`logs/pbandai_hk.log`)
- Optional email notify
- Optional keyword poll via `GET /api/search` with `X-G1-Area-Code: hk`

**Login is not required.** Default path is guest click farm (`CLICK_FARM=1`, `AUTO_ATC=0`).

## Mode overview

| Mode | When | What it does |
|---|---|---|
| Click farm (`CLICK_FARM=1`, `AUTO_ATC=0`) | Drop / pre-order race | Opens N guest Chromes; when PLACE PRE-ORDER appears, stops refreshing and leaves windows open for manual ATC/checkout; Discord ping |
| Auto-ATC (`CLICK_FARM=1`, `AUTO_ATC=1`) | Drop / pre-order race | Same farm, but auto-clicks PLACE PRE-ORDER then parks on cart |
| Monitor (`ENABLE_ADD_TO_CART=0`) | Watching stock only | Poll product page; print AVAILABLE / SOLD OUT / PREORDER / NOT OPEN |

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
The Windows zip now includes both `.env` and `.env.example` (drop-ready for `N2890904001`).

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
# edit .env — set DISCORD_WEBHOOK_URL
# (or put the webhook alone in discord_webhook.txt — survives .env reset)
```

## Quick use

```bash
# continuous click farm / monitor from .env
python web_shopping_bot_hk.py loop

# one pass
python web_shopping_bot_hk.py once

# check one product URL/code
python web_shopping_bot_hk.py check "https://p-bandai.com/hk/item/A2866726001"
```

### Click farm `.env` (recommended)

```env
AREA_CODE=hk
ENABLE_ADD_TO_CART=1
CLICK_FARM=1
AUTO_ATC=0
BROWSER_INSTANCES=20
CLICK_AT_SECOND=0
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
PRODUCT_LINKS=https://p-bandai.com/hk/item/A2866726001
BACKGROUND_MODE=0
```

`AREA_CODE` must match the product's region (`hk` for `p-bandai.com/hk/...`).
A mismatch makes the sale-timing lookup 404 and the bot falls back to blind
checking — it warns at startup when that happens.

Each instance checks for PLACE PRE-ORDER at **:00 of every minute**.

### Products that are not on sale yet

The bot reads `orderStartDate` / `preOrderStatus` from Bandai's product API and adapts:

- **Before the drop** it holds entirely (a PDP with no PLACE PRE-ORDER button is useless) and prints the sale time with a countdown, keeping the windows warm
- **~25s before** the drop it reloads the PDP so the button renders in time
- **At the drop** it switches to a fast-retry burst — every ~2.5s for 2 minutes — instead of waiting for the next `:00`, because the button often appears a few seconds late and a missed minute is a missed drop

```env
PRERELEASE_WAIT=1           # hold until orderStartDate
PREDROP_REFRESH_SECONDS=25  # PDP reload before T-0
DROP_BURST_SECONDS=120      # fast-retry window after the drop
DROP_BURST_INTERVAL=2.5     # retry spacing inside that window
```

### What happens when the pre-order button appears (default)

1. The farm **stops refreshing and clicking** — no auto ATC
2. **All Chrome windows stay open** on the product page
3. Discord gets a "PRE-ORDER BUTTON LIVE" ping
4. **You** click PLACE PRE-ORDER in a window, log in, and check out

Windows are never closed by the bot once the button is live: Chrome is launched
detached from chromedriver, `close()` keeps every open window, and the bot waits
for Ctrl+C. Finish checkout whenever you like.

Set `AUTO_ATC=1` only if you want the bot to click PLACE PRE-ORDER itself (then
it parks that window on `/{area}/cart`).

```env
# 0 = detect button → stop (default); 1 = auto-click PLACE PRE-ORDER
AUTO_ATC=0
# After ATC (AUTO_ATC=1 only), park the winning window on the cart page
PARK_ON_CART=1
# Never auto-close windows after button-live / cart (default 1)
KEEP_BROWSER_OPEN=1
```

Optional: start instances already signed in:

```env
FARM_COOKIE_FILES=sessions/acct1.json,sessions/acct2.json
```

### Monitor-only `.env` example

```env
PRODUCT_LINKS=https://p-bandai.com/hk/item/A2742450001
SALE_STATUSES=On,Waiting
ENABLE_ADD_TO_CART=0
LOG_FILE=logs/pbandai_hk.log
RETRY_WAIT=60
```

Errors/tracebacks are appended to `logs/pbandai_hk.log`.

## Proxies (optional)

```env
PROXY_URL=http://user:pass@1.2.3.4:8080
# or fill proxy.csv — instances round-robin the pool
PROXY_CSV=proxy.csv
```

Accepted proxy formats: `host:port:user:pass`, `http://user:pass@host:port`, `socks5://host:1080`.

## Important `.env` knobs

| Key | Purpose |
|---|---|
| `PRODUCT_LINKS` | Direct HK item URLs (comma-separated) |
| `PRODUCT_CODES` | Bare product codes (comma-separated) |
| `ENABLE_ADD_TO_CART` | `0` monitor only, `1` cart / click farm |
| `CLICK_FARM` | `1` = guest N-browser farm (default) |
| `AUTO_ATC` | `0` = stop when PLACE PRE-ORDER appears, leave Chrome open (default); `1` = auto-click |
| `BROWSER_INSTANCES` | How many Chromes to open (e.g. `20`) |
| `CLICK_AT_SECOND` | Wall-clock second to check each minute (`0` = :00). `-1` = use interval |
| `CLICK_INTERVAL_SECONDS` | Only used when `CLICK_AT_SECOND=-1` |
| `STOP_ON_FIRST_CART` | `AUTO_ATC=1` only: `0` keep racing; `1` stop after first cart |
| `KEEP_BROWSER_OPEN` | `1` = leave Chrome open after button-live / cart (default) |
| `OPEN_STAGGER_SECONDS` | Delay × instance index before Chrome launch + PDP (cuts PNA/WAF); jitter added |
| `OPEN_PDP_RETRIES` | Retries when first PDP visit is 500 / PAGE NOT AVAILABLE |
| `OPEN_PDP_RETRY_WAIT` | Base wait between PDP retries (grows + jitter per attempt) |
| `OOS_REFRESH_SECONDS` | Hard-refresh while waiting if UI shows OOS/PNA (jittered per instance) |
| `PDP_MAX_CONCURRENT` | Max simultaneous PDP/home navigations (default 3; cuts heal stampede) |
| `IDLE_ACTIVITY_SECONDS` | Scroll up/down while waiting (keep session alive; never clicks) |
| `DISCORD_WEBHOOK_URL` | Discord webhook; button-live / cart alert. Also reads `discord_webhook.txt` if .env is empty. Always saved to `logs/cart_successes.log` |
| `BACKGROUND_MODE` | `0` headed (recommended), `1` headless |
| `PROXY_URL` / `PROXY_CSV` | Optional proxies |
| `SEARCH_KEYWORDS` | Optional keywords for `/api/search` |
| `SALE_STATUSES` | Usually `On,Waiting` |
| `LOG_FILE` / `LOG_LEVEL` | Logging path and level |
| `EMAIL_USER` | Leave empty to skip email |

## Drop notes (click farm)

### Target example: `N2890904001` (GUNDAM CARD GAME 1ST ANNIVERSARY SET)
- Tiny stock; site sits behind F5 / Shape — **you click the real button** in headed Chrome
- Guest mode: no login required to watch. Set Discord webhook before the drop
- Start early so all windows are parked on the PDP

Flow:
1. Put webhook URL in `.env` (`AUTO_ATC=0`)
2. Optional: fill `proxy.csv`
3. Start menu `[1]` / `loop` — farm opens browsers and checks at **:00** every minute
4. When PLACE PRE-ORDER appears: Discord pings, farm **stops**, Chrome stays open for your manual ATC + checkout

Launcher menu:
- `[1]` Start click farm / monitor loop
- `[4]` Edit `.env`
- Login menu items removed (guest mode)

If Windows shows `WinError 193` starting Chrome:
- Install/update **Google Chrome** or **Microsoft Edge**
- Delete broken driver cache: `%USERPROFILE%\.wdm`
- Set `BROWSER=edge` in `.env` and retry

Cart/checkout still follows P-Bandai / Global-e site rules.

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
  OneClickMonitor.bat      # Windows one-click loop
  setup_and_launch.sh      # macOS/Linux one-click
  launch.py                # Python launcher fallback
  build_launcher.sh        # rebuild dist/PBandaiHK(.exe)
  dist/PBandaiHK.exe       # Windows launcher
  dist/PBandaiHK           # Linux launcher
  web_shopping_bot_hk.py   # CLI entry
  requirements.txt
  .env.example
  proxy.example.csv        # copy to proxy.csv (proxy pool)
  logs/                    # created at runtime
  launcher/main.go         # launcher source
  pbandai_hk/
    click_farm.py          # guest N-browser PLACE PRE-ORDER watch/farm + Discord
    bot.py                 # scan / match / click farm loop
    config.py
    browser_cart.py        # button detect / click helpers
    diagnostics.py
    ...
```
