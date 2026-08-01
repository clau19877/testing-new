# Premium Bandai USA signup helper

Create **multiple** Premium Bandai USA accounts from a CSV queue.

- **Accounts live in `task.csv`** (not in `config.json`)
- human-paced Safari actions (helps with Shape bot protection)
- iCloud IMAP for email auth codes
- **GrizzlySMS** phone + SMS OTP ([services](https://grizzlysms.com/services), code `bvq`)

## Quick start (macOS)

```bash
cp config.example.json config.json
cp task.example.csv task.csv
```

1. `config.json` = shared settings only (no account emails/passwords/profile)  
   - iCloud IMAP app-specific password (inbox that receives Bandai emails)  
   - GrizzlySMS API key  
   - site URLs (`base_url` / `register_url`)  

2. `task.csv` = **all account data**, one row per account (email + password required):

```csv
email,password,first_name,last_name,month,day,year,phone,address1,address2,city,state,zip,country
account1@icloud.com,PassOne!234,Alex,Example,01,15,1990,,123 Main St,,Los Angeles,CA,90001,United States
account2@icloud.com,PassTwo!234,Sam,Example,03,22,1992,,123 Main St,,Los Angeles,CA,90001,United States
account3@icloud.com,PassThree!234,Jordan,Example,07,08,1995,,123 Main St,,Los Angeles,CA,90001,United States
```

Leave `phone` empty when GrizzlySMS is enabled (a number is rented per row).

3. Safari permissions:
   - Accessibility for Terminal / Script Editor
   - Allow controlling Safari + System Events
   - Develop → **Allow JavaScript from Apple Events**

4. Run:

```bash
./run.command
```

The script processes **every row** in `task.csv` until the queue is empty.

## What goes where

| File | Put this here |
|---|---|
| `task.csv` | All account fields: email, password, name, DOB, address, etc. |
| `config.json` | iCloud IMAP, Grizzly API, site URLs, humanize/queue settings |
| `success.csv` | Auto-created: successful accounts (row removed from `task.csv`) |
| `failed.csv` | Auto-created: failures + reason (row removed from `task.csv`) |
| `logs/errors.jsonl` | Structured errors for debugging |

## Queue behavior

For each `task.csv` row:

1. Sign up with that row’s email/password  
2. Read email code from iCloud IMAP  
3. Rent GrizzlySMS number (`bvq`) and fill phone  
4. Enter SMS code if the site asks  
5. **Success** → append `success.csv`, delete row from `task.csv`  
6. **Failure** → append `failed.csv`, delete row from `task.csv`  
7. Cool down, then next row  

End-of-account Success/Failed confirm dialog is on by default (`queue.ask_confirm_success`).

Helpers:

```bash
python3 csv_queue.py --dir . count    # how many left
python3 csv_queue.py --dir . next     # peek next row
python3 grizzly_sms.py balance
python3 signup_log.py tail-errors -n 20
```

## Notes on reliability

- `failed.csv`'s `reason` column is now prefixed with the failing step, e.g. `[step:grizzly_rent] ...`, for faster triage.
- State/Country fields are auto-detected as `<select>` dropdowns when present and matched by option text/value, not just typed as raw keystrokes.
- If the site takes longer than usual to show the SMS/OTP screen, raise `grizzly.sms_screen_wait_tries` / `grizzly.sms_screen_wait_interval_ms` in `config.json`.
- `p-bandai.com` is a Vue/Vite single-page app: the initial HTML is an empty shell (`#app`), all screens render client-side. After every navigation the script now waits for `#app` to actually mount content, not just for `document.readyState`.
- A OneTrust cookie-consent banner is auto-dismissed if present (common Accept/Reject button IDs). Global-e (shipping/currency) popups are not auto-handled — if you see one blocking the flow, dismiss it manually and let us know the button text/selector so it can be added.

## Error logging

| File | Contents |
|---|---|
| `logs/errors.jsonl` | Structured ERROR records |
| `logs/signup.log` | Human-readable INFO/ERROR trail |

## Shape / anti-bot notes

Tune `config.json` → `humanize` delays/typing. Tips:

- Auth emails must land in the configured iCloud mailbox (aliases/forwarding OK)
- Don’t disable cool-downs between rows
- Prefer confirming the final Success/Failed dialog yourself

## Limits

- USA storefront only (`p-bandai.com/us`)
- Japan (`p-bandai.jp`) needs JP IP + JP SMS — out of scope
- Profile autofill is best-effort if Bandai changes field labels
