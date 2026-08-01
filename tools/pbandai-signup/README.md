# Premium Bandai USA signup helper

Personal Safari helper for Premium Bandai USA registration:

- CSV task queue (`task.csv` → `success.csv` / `failed.csv`)
- human-paced UI actions (helps with Shape bot protection)
- iCloud IMAP polling for the email authentication code
- **GrizzlySMS** phone numbers + SMS OTP ([services](https://grizzlysms.com/services), Premium Bandai code `bvq`)

## Setup (macOS)

1. Copy config + task files:

```bash
cp config.example.json config.json
cp task.example.csv task.csv
```

2. Create an **Apple app-specific password**  
   [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security → App-Specific Passwords  
   Put it in `config.json` → `icloud.app_specific_password`.

3. Put your **GrizzlySMS API key** in `config.json` → `grizzly.api_key`  
   Service defaults to `bvq` (PREMIUM BANDAI). Pick country on [grizzlysms.com/services](https://grizzlysms.com/services) and set `grizzly.country` (USA commonly `12`).

4. Edit `task.csv` with the accounts you want to create:

```csv
email,password,first_name,last_name,month,day,year,phone,address1,address2,city,state,zip,country
you@icloud.com,ChangeMe!234,Alex,Example,01,15,1990,,123 Main St,,Los Angeles,CA,90001,United States
```

Only `email` and `password` are required. Leave `phone` empty when GrizzlySMS is enabled (number is rented per row). Other columns override `config.json` profile defaults when present.

5. Permissions / Safari:
   - System Settings → Privacy & Security → **Accessibility** for Terminal / Script Editor
   - Automation: allow controlling **Safari** and **System Events**
   - Safari → Develop → enable **Allow JavaScript from Apple Events**

6. Run:

```bash
./run.command
```

## CSV queue behavior

| File | Purpose |
|---|---|
| `task.csv` | Pending work queue |
| `success.csv` | Successfully created accounts (row removed from `task.csv`) |
| `failed.csv` | Failed attempts + reason (row removed from `task.csv`) |

Flow per row:

1. Read first row from `task.csv`
2. Run signup in Safari (humanized)
3. Pull email auth code from iCloud IMAP
4. Rent a GrizzlySMS number (`bvq`) and fill phone
5. If an SMS/OTP screen appears, poll GrizzlySMS for the code and enter it
6. On success → append `success.csv` (includes phone + activation_id), **delete row** from `task.csv`
7. On failure → append `failed.csv`, cancel unused Grizzly activation, **delete row** from `task.csv`
8. Cool down, then continue with the next row

By default the script asks you to confirm Success/Failed at the end of each account (`queue.ask_confirm_success` in config).

Inspect queue / SMS helpers:

```bash
python3 csv_queue.py --dir . count
python3 csv_queue.py --dir . next
python3 grizzly_sms.py balance
python3 grizzly_sms.py rent
```

## Error logging

Every failure is recorded for later improvement:

| File | Contents |
|---|---|
| `logs/errors.jsonl` | Structured ERROR records (one JSON object per line) |
| `logs/signup.log` | Human-readable INFO + ERROR trail |

Each error includes timestamp, step name (e.g. `fetch_icloud_code`, `grizzly_rent`), email, phone, activation id, and message.

```bash
# latest errors
python3 signup_log.py tail-errors -n 20

# manual note
python3 signup_log.py write --level ERROR --step manual --message "Shape challenge appeared"
```

## Shape / anti-bot notes

Humanization knobs in `config.json` → `humanize`:

| Key | Purpose |
|---|---|
| `warmup_browse` | Visit storefront and scroll before register |
| `min/max_action_delay_ms` | Pause between steps |
| `min/max_key_delay_ms` | Per-key typing cadence |
| `think_pause_chance` | Extra “reading” pauses |
| `typo_chance` | Tiny mistype + backspace |
| `scroll_chance` | Random scroll activity |
| `mouse_wiggle` | Light keyboard nudge activity |

Tips:

- Auth codes must arrive in the **configured iCloud mailbox** (aliases/forwarding OK)
- Don’t hammer retries; leave cool-downs enabled
- Prefer confirming the final success dialog yourself

## IMAP-only test

```bash
python3 fetch_icloud_code.py --once --to-email 'you@icloud.com'
```

## Limits

- Built for **Premium Bandai USA** (`p-bandai.com/us`)
- Japan (`p-bandai.jp`) needs JP IP + JP SMS and is out of scope
- DOM labels change; profile autofill is best-effort
- If Shape presents a hard challenge, finish that step manually and use the Success/Failed prompt
