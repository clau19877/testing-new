# Premium Bandai USA signup helper

Personal Safari helper for Premium Bandai USA registration:

- human-paced UI actions (helps with Shape bot protection)
- iCloud IMAP polling for the email authentication code

## Setup (macOS)

1. Copy config:

```bash
cp config.example.json config.json
```

2. Create an **Apple app-specific password**  
   [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security → App-Specific Passwords  
   Use that value in `icloud.app_specific_password` (not your normal Apple ID password).

3. Fill `config.json`:
   - iCloud IMAP email/password
   - Premium Bandai signup email (usually same iCloud address)
   - password + profile fields

4. Permissions:
   - System Settings → Privacy & Security → **Accessibility** for Terminal / Script Editor
   - Automation: allow controlling **Safari** and **System Events**

5. Run:

```bash
./run.command
```

Or:

```bash
osascript Signup.applescript
```

## What it does

1. Opens Premium Bandai US and optionally warms up (scroll/browse)
2. Goes to `/us/register`, accepts 18+ age gate
3. Types email with human-like key delays / occasional typo corrections
4. Submits and waits for the auth-code screen
5. Polls iCloud IMAP (`imap.mail.me.com:993`) for the Bandai code
6. Types the code and continues
7. Best-effort fill of profile fields, then pauses for your review before final confirm

## Shape / anti-bot notes

This is intentionally **not** headless and avoids dumping values via pure JS `.value = ...`.

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

Tips that help session trust:

- Use normal Safari (logged-in iCloud, real profile, history)
- Don’t hammer retries; one careful pass is better
- Keep final Confirm manual if Shape challenges you
- Use your real info / one account only (P-Bandai enforces one membership per person)

## IMAP-only test

```bash
python3 fetch_icloud_code.py --once
```

Poll until a fresh code arrives:

```bash
python3 fetch_icloud_code.py --since-epoch "$(date +%s)"
```

## Limits

- Built for **Premium Bandai USA** (`p-bandai.com/us`)
- Japan (`p-bandai.jp`) needs JP IP + JP SMS and is out of scope
- DOM labels change; profile autofill is best-effort
- If Shape presents a hard challenge, finish that step manually
