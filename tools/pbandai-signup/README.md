# Premium Bandai USA signup helper

Personal Safari helper for Premium Bandai USA registration:

- CSV task queue (`task.csv` → `success.csv` / `failed.csv`)
- human-paced UI actions (helps with Shape bot protection)
- iCloud IMAP polling for the email authentication code

## Setup (macOS)

1. Copy config + task files:

```bash
cp config.example.json config.json
cp task.example.csv task.csv
```

2. Create an **Apple app-specific password**  
   [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security → App-Specific Passwords  
   Put it in `config.json` → `icloud.app_specific_password`.

3. Edit `task.csv` with the accounts you want to create:

```csv
email,password,first_name,last_name,month,day,year,phone,address1,address2,city,state,zip,country
you@icloud.com,ChangeMe!234,Alex,Example,01,15,1990,5551234567,123 Main St,,Los Angeles,CA,90001,United States
```

Only `email` and `password` are required. Other columns override `config.json` profile defaults when present.

4. Permissions / Safari:
   - System Settings → Privacy & Security → **Accessibility** for Terminal / Script Editor
   - Automation: allow controlling **Safari** and **System Events**
   - Safari → Develop → enable **Allow JavaScript from Apple Events**

5. Run:

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
3. Pull auth code from iCloud IMAP for that email
4. On success → append `success.csv`, **delete row** from `task.csv`
5. On failure → append `failed.csv`, **delete row** from `task.csv`
6. Cool down, then continue with the next row

By default the script asks you to confirm Success/Failed at the end of each account (`queue.ask_confirm_success` in config).

Inspect queue:

```bash
python3 csv_queue.py --dir . count
python3 csv_queue.py --dir . next
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
