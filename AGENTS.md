# AGENTS.md

## Cursor Cloud specific instructions

This repo contains two independent components:

1. **IBC Solidity contracts template** (root) — Hardhat + Foundry project. This is the primary
   application. Standard commands live in `README.md` and the `Justfile` (`just compile`,
   `just compile foundry`, `just deploy`, `just send-packet`, etc.). Deploy/send recipes target
   live Base/OP Sepolia testnets and need funded keys + API keys in `.env` (see `.env.example`).
2. **`tools/riot-email-update/`** — a Python/Playwright tool that logs into Riot Games and changes
   an account email. Its core flow drives Riot's live site and defeats hCaptcha via paid solver
   services + a vision model, and needs real Riot credentials, IMAP mailboxes, residential proxies,
   and captcha API keys (`.env.example`). Do not run this live flow in cloud agents; limit to
   `--help`, imports, and headless Playwright launch checks unless a human explicitly requests otherwise.

### Environment / PATH caveats (non-obvious)

- `forge`/`cast`/`anvil` are in `~/.foundry/bin` and `just` is in `~/.local/bin`. Non-interactive
  shells may not have these on `PATH`; prefix commands with
  `export PATH="$HOME/.foundry/bin:$HOME/.local/bin:$PATH"`.
- Foundry compilation needs the git submodules under `lib/` (`forge-std`, `vibc-core-smart-contracts`).
  The update script runs `git submodule update --init --recursive`; if compiles fail with missing
  imports, re-run it.
- `hardhat.config.js` puts `PRIVATE_KEY_1` into the network `accounts` array, so Hardhat **fails to
  load its config** (even for `compile`) if `.env` has an empty/invalid `PRIVATE_KEY_1`. Copy
  `.env.example` to `.env` and set `PRIVATE_KEY_1` to any valid 32-byte hex key for local work.
- Node 22 prints an "unsupported by Hardhat" warning; it is harmless — compiles/tests still work.
- The template ships **no contract tests** (`test/` does not exist), so `forge test` / `npx hardhat test`
  report 0 tests. There is no configured linter (no solhint/eslint/prettier config).

### Local end-to-end demo (no external services)

Start a local chain and exercise the counter contract without testnets:
`anvil` (background), then `forge create contracts/XCounterUC.sol:XCounterUC ... --constructor-args <mw>`
and `cast send <addr> "sendUniversalPacket(address,bytes32,uint64)" ...` increments `counter()`.
A mock middleware contract is required for `sendUniversalPacket` to succeed locally.

### Python tool

The venv lives at `tools/riot-email-update/.venv`. `pytesseract` needs the system `tesseract-ocr`
binary and Playwright needs its Chromium browser (both installed during environment setup, persisted
in the snapshot). Run tool commands with that venv activated.
