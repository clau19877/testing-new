# Amazon JP Account Creator

Create **amazon.co.jp** buyer accounts with Playwright, using emails/identities you own.

## Create one account

```bash
cd amazon-jp-account-system
npm install
npx playwright install chromium

# Option A: inline identity (recommended for a first live test)
npm run create-one -- \
  --email you@your-domain.example \
  --password 'YourStrongPass1' \
  --name '山田 太郎' \
  --headed

# Option B: first entry in identities JSON
cp config/identities.example.json config/identities.json
# edit config/identities.json, then:
npm run create-one -- --identity-id identity-001 --headed
```

Flow:
1. Opens amazon.co.jp register page
2. Fills email → continue
3. Fills name + password
4. Prompts you in the terminal for the OTP Amazon emailed/SMS’d
5. Submits OTP and saves the result to `data/accounts.json`
6. Writes screenshots under `data/artifacts/` on success/error

Dry-run (no Amazon traffic):

```bash
npm run create-one:dry -- --email you@example.com --password 'Pass1234' --name 'Test User'
```

## Captcha / puzzle

Amazon often shows a **「クイズを開始する」** puzzle after password submit. With `--headed` (default for `create-one`), the tool pauses and waits while you solve it in the browser, then continues to the OTP prompt.

Headless runs will stop with a clear error if a puzzle appears.


## Other commands

```bash
npm run dry-run          # parallel dry-run of sample identities
npx tsx src/cli.ts list  # show stored accounts
```

## Layout

```text
src/
  adapters/amazon.ts    # live Playwright signup flow
  adapters/browser.ts   # Playwright / dry-run / CDP
  adapters/mailbox.ts   # prompt OTP / dry-run / IMAP stub
  cli.ts                # create-one | run | list
  core/orchestrator.ts  # worker pool (create-one uses concurrency=1)
```

## Config

`config/default.json` defaults to Playwright + terminal OTP prompt for live creation.
