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
email,password,first_name,last_name,gender,month,day,year,phone,phone_country_iso,address1,address2,city,state,zip,country
account1@icloud.com,Bnd4iFn7Kp,Alex,Example,NotSelected,1,15,1990,,,123 Main St,,Los Angeles,CA,90001,United States
account2@icloud.com,Xq9wLm2Vhz,Sam,Example,NotSelected,3,22,1992,,,123 Main St,,Los Angeles,CA,90001,United States
account3@icloud.com,Rt5cWk8Ndp,Jordan,Example,NotSelected,7,8,1995,,,123 Main St,,Los Angeles,CA,90001,United States
```

Leave `phone` empty when GrizzlySMS is enabled (a number is rented per row).

### Per-account email addresses (iCloud Hide My Email)

Instead of a single real inbox for every account, you can use an iCloud **Hide My Email** alias as the `email` value in each `task.csv` row (create them via Settings → your name → iCloud → Hide My Email, or Safari's autofill suggestion on a signup form). Apple forwards mail sent to each alias into the same primary inbox your `config.json` `icloud` credentials poll — no extra IMAP config needed. The email confirmation flow has been verified against a real Premium Bandai email delivered this way, including the address-relay format Apple uses for the `From` header and the blockquote-style plain text some clients show.

`gender` accepts (case-insensitive, matches the site's own radio values): `Male`, `Female`, `NotApplicable` (Non-binary), `NotSelected` (Prefer not to say). It's **required** by the real form and defaults to `NotSelected` if left blank.

### Password rules (from the live site)

The real ENTER INFORMATION screen enforces:
- 8–20 characters
- No character repeated 3+ times in a row
- No 3+ sequential characters (e.g. `abc`, `321`)
- At least 3 of: uppercase, lowercase, digits, symbols (allowed symbols: `` ` ~ ! @ # $ % ^ & * ( ) _ - . ' ``)

Pick passwords that satisfy these — something like `Pass1234` would be rejected (`234` is a sequential run).

### Random data

Put the word `random` (case-insensitive) in any `task.csv` cell and it's replaced with a generated value from a curated pool before the row is processed — the resolved value is written back into `task.csv` immediately, so `success.csv`/`failed.csv` record what was actually submitted, not the literal word "random".

| Field | Behavior |
|---|---|
| `first_name`, `last_name` | Picked from a name pool |
| `gender` | Random of `Male`/`Female`/`NotApplicable`/`NotSelected` |
| `month`, `day`, `year` | Generated together as a valid, 18+ date of birth (if only some of the three are `random`, the fixed ones are kept and `day` still respects the resulting month) |
| `city`, `state`, `zip` | If 2+ of these are `random` in the same row, one consistent US city/state/zip triple is used; if only one is `random`, it's resolved independently |
| `address1` | Random street address |
| `password` | Generated to satisfy the site's real password rules (see above), retried internally until compliant |
| `country` | Weighted random between `United States`/`Canada` (matches the "Area" dropdown's only two options) |
| `phone` | Cleared to empty (relies on GrizzlySMS renting a real number; a fake number can't receive the SMS code) |
| `email` | **Requires `random_data.email_template` in `config.json`** (e.g. `"youralias+{token}@yourdomain.com"`) — errors clearly if `random` is used without one configured. Plain `name@icloud.com` does **not** support `+` aliasing; this only works with a provider/domain that does (custom iCloud+ domain, Fastmail, Gmail, etc.) |

Example row:

```csv
account2@icloud.com,random,random,random,random,random,random,random,,random,,random,random,random,random
```

Note: Premium Bandai's own terms limit membership to one account per person — this feature is for varying test/profile data across accounts you're entitled to create, not for evading that policy.

### What the ENTER INFORMATION screen actually asks for

Inspecting the live form: First/Last Name, an "Area" country dropdown (Canada/US **only**), an "International Dialing Code" dropdown (many countries) + Phone Number, Date of Birth, Gender (radio, required), Password, and a required Terms of Use checkbox. There is **no** street address / city / zip field at this step — `address1`/`address2`/`city`/`state`/`zip` columns are kept for forward-compatibility and are harmless no-ops if the site doesn't render them.

- **Area** is driven by `task.csv` `country` (mapped to `US`/`CA` — the only two options that exist).
- **International Dialing Code** is driven independently:
  1. `task.csv` `phone_country_iso` if set (explicit override)
  2. otherwise, auto-detected from the rented GrizzlySMS number's country (see below)
  3. otherwise, falls back to the same value as Area

This matters because a GrizzlySMS phone number's *country* doesn't have to match the site's *home region* — e.g. you can register as a US-area member (`Area` = US) while using a UK-sourced number for SMS verification (`International Dialing Code` = UK).

#### GrizzlySMS country → dialing code mapping

`grizzly_sms.py` maps its own numeric country id to the ISO code the dropdown needs (these ids are GrizzlySMS-specific and unrelated to phone dial codes):

| GrizzlySMS `country` | Dialing code dropdown value |
|---|---|
| `12` (USA) | `US` |
| `16` (UK) | `IM` — **Isle of Man [+44]**, confirmed as the working option for UK numbers, not `GB` |

To use UK numbers, set in `config.json`:
```json
"grizzly": { "country": 16 }
```
Override the mapping entirely with `grizzly.phone_dropdown_iso` in `config.json`, or per-row with `task.csv`'s `phone_country_iso` column, if GrizzlySMS adds new countries not yet in the built-in table. The required Terms of Use checkbox is checked automatically.

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

## Confirmed fixes from real-world testing

- **Empty message content on some iCloud accounts**: the legacy `FETCH (RFC822)` IMAP command returns genuinely empty content (`* n FETCH ()`) on some real iCloud accounts, even though the message exists and has real content. Switched to `FETCH (BODY.PEEK[])` (the modern IMAP4rev1 equivalent, confirmed working end-to-end against a real 1600-message mailbox) — also better behaved since `.PEEK` avoids marking scanned messages as read.
- **Long-running shell calls could be aborted early**: `fetchICloudCode` and the GrizzlySMS SMS wait can legitimately run for minutes, longer than AppleScript's default 2-minute Apple Event timeout. Both are now wrapped in explicit `with timeout of ...` blocks.
- Added `extract_rfc822_bytes()` to robustly parse whatever shape an IMAP FETCH response comes back in (plain bytes vs. tuple-literal, and tolerant of an interleaved flag-update response) instead of assuming a fixed shape.

## IMAP troubleshooting

If setup fails with an iCloud IMAP error, run the diagnostic directly for a clear explanation instead of a raw error:

```bash
python3 fetch_icloud_code.py --diagnose
```

It distinguishes the common causes:

| Symptom | Likely cause |
|---|---|
| "config.json still has placeholder iCloud credentials" | You haven't edited `icloud.email`/`icloud.app_specific_password` yet |
| "iCloud rejected the login" | Using your normal Apple ID password instead of an **app-specific password**, 2FA not enabled on the Apple ID, or a typo/stray space in `config.json` |
| "Could not reach ...:993" | No internet, or a firewall/VPN/proxy blocking outbound port 993 |
| "Connection to ...:993 timed out" | Same as above — something is blocking port 993 |

App-specific passwords are created at [appleid.apple.com](https://appleid.apple.com) → **Sign-In and Security → App-Specific Passwords** (requires Two-Factor Authentication enabled on the Apple ID).

Once `--diagnose` reports OK, test a single mailbox scan for a specific email:

```bash
python3 fetch_icloud_code.py --once --to-email 'you@icloud.com'
```

`./run.command` also runs this diagnostic automatically before launching Safari, so a bad iCloud config is caught before any account is attempted.

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
| `logs/signup.log` | Human-readable INFO/WARNING/ERROR trail |
| `logs/trace.log` | **Everything, in and out** — every shell command run (redacted, never the real password/API key), every Safari JS call's result, every button/field match attempt with what it was looking for, every IMAP scan (per-message subject/from/relevance/code-extraction), every GrizzlySMS API request/response, and a `STEP_OK` marker after each stage of the signup flow completes |

`trace.log` is designed so a failure can be fully diagnosed from the log alone — you can see exactly which button/label/selector matched or didn't, what every intermediate value was, and where the flow actually stopped, without needing to reproduce the run. Secrets (account password, iCloud app-specific password, GrizzlySMS API key) are never written to any log file — callers pass a pre-redacted description instead of the raw command, and password-like fields are masked as `[REDACTED, N chars]`.

Example `trace.log` excerpt:

```
... | DEBUG | STEP_OK | age_gate
... | DEBUG | FOCUS_ATTEMPT | selectors=[...]
... | DEBUG | JS_RESULT | ok:input[type='email']
... | DEBUG | STEP_OK | email_entry email=you@icloud.com
... | DEBUG | SHELL_OUT | fetch_icloud_code since_epoch=1785... to_email=you@icloud.com
... | DEBUG | mailbox_scan | msg 1600: subject='[PREMIUM BANDAI] ...' relevant=True
... | DEBUG | mailbox_scan | msg 1600: code_extracted='047723'
... | DEBUG | STEP_OK | fetch_icloud_code code=047723
```

## Shape / anti-bot notes

Tune `config.json` → `humanize` delays/typing. Tips:

- Auth emails must land in the configured iCloud mailbox (aliases/forwarding OK)
- Don’t disable cool-downs between rows
- Prefer confirming the final Success/Failed dialog yourself

## Limits

- USA storefront only (`p-bandai.com/us`)
- Japan (`p-bandai.jp`) needs JP IP + JP SMS — out of scope
- Profile autofill is best-effort if Bandai changes field labels
