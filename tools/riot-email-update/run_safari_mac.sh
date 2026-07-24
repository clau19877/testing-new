#!/usr/bin/env bash
# Launch Riot email-update via local macOS Safari (usually no hCaptcha).
#
# REQUIRED for CSV accounts (no username prompts):
#   ./run_safari_mac.sh
#   ./run_safari_mac.sh data/tasks.csv
#   ./run_safari_batch.sh data/tasks.csv
#
# Do NOT double-click safari_riot_email.applescript — that has no CSV.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export TOOL_DIR="$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This launcher is for macOS Safari only (uname=$(uname -s))." >&2
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

csv_has_rows() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  # any non-empty, non-comment line after header
  awk 'NR>1 && $0 !~ /^[[:space:]]*$/ && $0 !~ /^#/ { found=1; exit } END { exit !found }' "$f"
}

CSV_CANDIDATE=""
if [[ $# -ge 1 && "$1" != --* ]]; then
  if [[ -f "$1" ]]; then
    CSV_CANDIDATE="$1"
    shift
  elif [[ -f "$ROOT/$1" ]]; then
    CSV_CANDIDATE="$ROOT/$1"
    shift
  else
    echo "File not found: $1" >&2
    exit 2
  fi
elif [[ -f "$ROOT/data/tasks.csv" ]]; then
  CSV_CANDIDATE="$ROOT/data/tasks.csv"
fi

if [[ -n "$CSV_CANDIDATE" ]]; then
  if ! csv_has_rows "$CSV_CANDIDATE"; then
    echo "ERROR: $CSV_CANDIDATE has no account rows (only header or empty)." >&2
    echo "  Add lines like:" >&2
    echo "  riotuser,password,imap@icloud.com,app-pass,new@icloud.com,imap.mail.me.com,993,," >&2
    echo "  Example: data/tasks.csv.example" >&2
    exit 2
  fi
  echo "== Riot Safari batch (from CSV) =="
  echo "  csv: $CSV_CANDIDATE"
  echo "  usernames come from the CSV — you should NOT get a username dialog"
  echo
  exec python3 "$ROOT/run_safari_batch.py" "$CSV_CANDIDATE" "$@"
fi

# Single-account path only when there is no data/tasks.csv
ENTRY_URL="${LOGIN_ENTRY_URL:-${LOGIN_URL:-https://docs.qq.com/scenario/link.html?url=https%3A%2F%2Faccount.riotgames.com%2F&pid=300000000%24KrVGtggzglZK&cid=144115210422737002&nlc=1}}"
USER_NAME="${RIOT_USERNAME:-}"
PASS="${RIOT_PASSWORD:-}"
NEW_EMAIL_ADDR="${NEW_EMAIL:-}"

PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --username|--password|--new-email|--mfa-code|--verify-code|--entry-url)
      PASSTHROUGH+=("$1" "$2"); shift 2 ;;
    --skip-email-change|--batch)
      PASSTHROUGH+=("$1"); shift ;;
    *)
      echo "Unknown arg: $1" >&2
      echo "Put accounts in data/tasks.csv then run: $0" >&2
      exit 2 ;;
  esac
done

ARGS=(--entry-url "$ENTRY_URL")
[[ -n "$USER_NAME" ]] && ARGS+=(--username "$USER_NAME")
[[ -n "$PASS" ]] && ARGS+=(--password "$PASS")
[[ -n "$NEW_EMAIL_ADDR" ]] && ARGS+=(--new-email "$NEW_EMAIL_ADDR")
ARGS+=("${PASSTHROUGH[@]}")

if [[ -z "$USER_NAME" ]]; then
  echo "ERROR: no data/tasks.csv and no RIOT_USERNAME." >&2
  echo "  Create data/tasks.csv with account rows, then run: $0" >&2
  exit 2
fi

echo "== Riot Safari single-account =="
echo "  user: $USER_NAME"
exec osascript "$ROOT/safari_riot_email.applescript" -- "${ARGS[@]}"
