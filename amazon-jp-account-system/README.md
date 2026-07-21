# Amazon JP Account Creator

Create **amazon.co.jp** buyer accounts with Playwright, using emails/identities you own.

## Create one account (IMAP OTP)

```bash
cd amazon-jp-account-system
npm install
npx playwright install chromium

cp .env.example .env
# edit .env: IMAP_HOST / IMAP_USER / IMAP_PASS for the inbox you own

# verify IMAP first
npx tsx src/cli.ts test-imap

npm run create-one -- \
  --email you@your-domain.example \
  --password 'YourStrongPass1' \
  --name '山田 太郎' \
  --headed \
  --imap
```

Flow:
1. Opens amazon.co.jp register page
2. Fills email → continue → name/password
3. Pauses for Amazon puzzle if shown (headed)
4. Polls your IMAP inbox for the Amazon OTP and submits it
5. Saves the result to `data/accounts.json`

Manual OTP instead of IMAP:

```bash
npm run create-one -- --email you@your-domain.example --password '...' --name '...' --otp-prompt
```

Dry-run (no Amazon / IMAP traffic):

```bash
npm run create-one:dry -- --email you@example.com --password 'Pass1234' --name 'Test User'
```

## IMAP settings

Set via `.env` (preferred) or `mailbox.imap` in `config/default.json`:

| Variable | Meaning |
|----------|---------|
| `IMAP_HOST` | IMAP server hostname |
| `IMAP_PORT` | Default `993` |
| `IMAP_SECURE` | TLS (`true` by default on 993) |
| `IMAP_USER` / `IMAP_PASS` | Mailbox credentials (use an app password when required) |
| `IMAP_MAILBOX` | Folder to poll (`INBOX`) |
| `IMAP_FROM_INCLUDES` | Comma filters for From: (default `amazon`) |
| `IMAP_LOOKBACK_MS` | How far back to search each poll |

`amz-jp test-imap` checks login + folder access.

## Captcha / puzzle

Amazon often shows a **「クイズを開始する」** puzzle after password submit. With `--headed` (default for `create-one`), the tool pauses while you solve it, then continues to IMAP OTP polling.

## Other commands

```bash
npm run dry-run          # parallel dry-run of sample identities
npx tsx src/cli.ts list  # show stored accounts
npx tsx src/cli.ts test-imap
```

## Layout

```text
src/
  adapters/amazon.ts    # live Playwright signup flow
  adapters/browser.ts   # Playwright / dry-run / CDP
  adapters/mailbox.ts   # IMAP / prompt / dry-run OTP
  utils/otp.ts          # OTP extraction from mail text
  cli.ts                # create-one | test-imap | run | list
```
