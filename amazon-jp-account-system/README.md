# Amazon JP Account System (own identities)

Parallel job orchestrator for **amazon.co.jp** account workflows using **identities and inboxes you own**. This is scaffolding for your own ops system — not a disposable/forged-identity farm.

## What this is

- Worker pool with configurable concurrency (`p-limit`)
- Sticky proxy assignment (1 identity ↔ 1 proxy when provided)
- Pluggable adapters: browser profile, mailbox OTP, Amazon register, account store
- Dry-run mode that exercises the full pipeline without contacting Amazon
- JSON identity source + local account store

## What this is not

- Not a captcha/SMS abuse toolkit
- Not a forged-document or temp-identity generator
- Live Amazon signup UI automation is left as a skeleton for you to wire against accounts you control

## Layout

```text
amazon-jp-account-system/
├── config/
│   ├── default.json              # concurrency, locale, retries
│   ├── identities.example.json   # replace with emails you own
│   └── proxies.example.txt       # optional sticky JP proxies
├── src/
│   ├── adapters/                 # browser, mailbox, proxy, amazon, identity
│   ├── core/orchestrator.ts      # parallel runner
│   ├── store/account-store.ts
│   └── cli.ts
└── data/                         # created at runtime (gitignored)
```

## Quick start

```bash
cd amazon-jp-account-system
npm install
cp config/identities.example.json config/identities.json
# edit identities.json with emails/phones you control

npm run dry-run
# or:
npx tsx src/cli.ts run --dry-run --count 3 --concurrency 2
npx tsx src/cli.ts list
```

## Parallel model

```text
identities.json ──► job queue
                       │
                       ▼
              worker pool (N)
                 │
     ┌───────────┼───────────┐
     ▼           ▼           ▼
  profile+    profile+    profile+
  proxy A     proxy B     proxy C
     │           │           │
     ▼           ▼           ▼
  mailbox OTP / Amazon adapter
     │
     ▼
  data/accounts.json
```

Defaults keep concurrency low (2). Raise only after your browser/mailbox adapters are stable.

## Wiring live adapters

1. **Browser (`src/adapters/browser.ts`)**  
   Point `CdpBrowser` at AdsPower / GoLogin / Multilogin: start profile → return `wsEndpoint`.

2. **Mailbox (`src/adapters/mailbox.ts`)**  
   Implement IMAP (or your provider API) against domains/inboxes you own.

3. **Amazon (`src/adapters/amazon.ts`)**  
   In `LiveAmazonAdapter`, `chromium.connectOverCDP(session.wsEndpoint)` and drive the official amazon.co.jp signup/login flow for the supplied identity.

4. Set config:

```json
{
  "browser": { "provider": "cdp", "headless": false },
  "mailbox": { "provider": "imap", "otpTimeoutMs": 120000, "pollIntervalMs": 3000 },
  "proxy": { "required": true, "sticky": true }
}
```

## CLI

| Command | Purpose |
|---------|---------|
| `run --dry-run` | Simulate parallel jobs |
| `run --concurrency 3 --count 5` | Cap workers and identity count |
| `run --force` | Re-process identities already in the store |
| `list` | Print `data/accounts.json` |

## Reference tools (architecture only)

Patterns borrowed from AdsPower/Multilogin multi-profile ops and register-orchestrators like any-auto-register (job queue, semaphore, proxy stickiness, SSE/logging). See the exploration notes in the PR description.
