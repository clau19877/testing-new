#!/usr/bin/env bash
# Launch Riot email-update via local macOS Safari (usually no hCaptcha).
#
# One-time Safari setup:
#   Safari → Settings → Advanced → show Develop menu
#   Develop → Allow JavaScript from Apple Events
#
# Usage:
#   cp .env.example .env   # fill RIOT_* / IMAP_* / NEW_EMAIL
#   ./run_safari_mac.sh
#   ./run_safari_mac.sh --skip-email-change
#   ./run_safari_mac.sh --username X --password Y --new-email Z

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

ENTRY_URL="${LOGIN_ENTRY_URL:-${LOGIN_URL:-https://docs.qq.com/scenario/link.html?url=https%3A%2F%2Faccount.riotgames.com%2F&pid=300000000%24KrVGtggzglZK&cid=144115210422737002&nlc=1}}"
USER_NAME="${RIOT_USERNAME:-}"
PASS="${RIOT_PASSWORD:-}"
NEW_EMAIL_ADDR="${NEW_EMAIL:-}"

ARGS=(
  "$ROOT/safari_riot_email.applescript"
  --entry-url "$ENTRY_URL"
)

# Pass through explicit CLI flags; otherwise fill from .env when present.
PASSTHROUGH=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --username|--password|--new-email|--mfa-code|--verify-code|--entry-url)
      PASSTHROUGH+=("$1" "$2"); shift 2 ;;
    --skip-email-change)
      PASSTHROUGH+=("$1"); shift ;;
    *)
      echo "Unknown arg: $1" >&2; exit 2 ;;
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

echo "== Riot Safari launcher =="
echo "  entry: $ENTRY_URL"
echo "  user:  ${USER_NAME:-"(prompt)"}"
echo "  new:   ${NEW_EMAIL_ADDR:-"(prompt / skip)"}"
echo "  Ensure: Safari → Develop → Allow JavaScript from Apple Events"
echo

exec osascript "${ARGS[@]}"
