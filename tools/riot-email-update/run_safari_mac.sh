#!/usr/bin/env bash
# Launch Riot email-update via local macOS Safari (usually no hCaptcha).
#
# Preferred (reads accounts from CSV, no username prompts):
#   ./run_safari_mac.sh
#   ./run_safari_mac.sh data/tasks.csv
#   ./run_safari_batch.sh data/tasks.csv
#
# Single account from .env / flags:
#   ./run_safari_mac.sh --username X --password Y --new-email Z
#   ./run_safari_mac.sh --skip-email-change

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

# Default: if first arg is a CSV (or no args and data/tasks.csv exists), run batch.
CSV_CANDIDATE=""
if [[ $# -ge 1 && -f "$1" && "$1" == *.csv ]]; then
  CSV_CANDIDATE="$1"
  shift
elif [[ $# -ge 1 && -f "$ROOT/$1" && "$1" == *.csv ]]; then
  CSV_CANDIDATE="$ROOT/$1"
  shift
elif [[ $# -eq 0 && -f "$ROOT/data/tasks.csv" ]]; then
  # Only auto-batch when CSV has at least one data row
  if awk 'NR>1 && $0 !~ /^[[:space:]]*$/ && $0 !~ /^#/ { found=1; exit } END { exit !found }' "$ROOT/data/tasks.csv"; then
    CSV_CANDIDATE="$ROOT/data/tasks.csv"
  fi
fi

if [[ -n "$CSV_CANDIDATE" ]]; then
  echo "== Riot Safari batch (from CSV) =="
  echo "  csv: $CSV_CANDIDATE"
  echo "  (usernames/passwords/new emails come from the CSV — no prompts)"
  echo
  exec python3 "$ROOT/run_safari_batch.py" "$CSV_CANDIDATE" "$@"
fi

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
      echo "Usage: $0 [data/tasks.csv]   OR   $0 --username U --password P --new-email E" >&2
      exit 2 ;;
  esac
done

have_flag() {
  local key="$1"
  local i
  for ((i=0; i<${#PASSTHROUGH[@]}; i++)); do
    [[ "${PASSTHROUGH[$i]}" == "$key" ]] && return 0
  done
  return 1
}

ARGS=()
ARGS+=(--entry-url "$ENTRY_URL")
if ! have_flag --username && [[ -n "$USER_NAME" ]]; then
  ARGS+=(--username "$USER_NAME")
fi
if ! have_flag --password && [[ -n "$PASS" ]]; then
  ARGS+=(--password "$PASS")
fi
if ! have_flag --new-email && [[ -n "$NEW_EMAIL_ADDR" ]]; then
  ARGS+=(--new-email "$NEW_EMAIL_ADDR")
fi
ARGS+=("${PASSTHROUGH[@]}")

echo "== Riot Safari single-account launcher =="
echo "  entry: $ENTRY_URL"
echo "  user:  ${USER_NAME:-"(missing — will prompt unless passed)"}"
echo "  new:   ${NEW_EMAIL_ADDR:-"(missing — will prompt unless passed)"}"
echo "  Tip: put rows in data/tasks.csv and run: ./run_safari_mac.sh"
echo "  Ensure: Safari → Develop → Allow JavaScript from Apple Events"
echo

# "--" stops osascript from eating --username / --password
exec osascript "$ROOT/safari_riot_email.applescript" -- "${ARGS[@]}"
