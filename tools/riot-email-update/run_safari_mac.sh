#!/usr/bin/env bash
# Launch Riot email-update via local macOS Safari (usually no hCaptcha).
#
# Put this whole folder anywhere, e.g.:
#   ~/Desktop/riotemail/
# with accounts in:
#   ~/Desktop/riotemail/data/tasks.csv
# then:
#   cd ~/Desktop/riotemail
#   ./run_safari_mac.sh
#
# Or double-click: RUN_ME.command
#
# Do NOT double-click safari_riot_email.applescript — that cannot see your CSV.

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
  awk 'NR>1 && $0 !~ /^[[:space:]]*$/ && $0 !~ /^#/ { found=1; exit } END { exit !found }' "$f"
}

find_tasks_csv() {
  local c
  # Explicit path from argv (caller may pass one)
  if [[ -n "${1:-}" && "$1" != --* ]]; then
    if [[ -f "$1" ]]; then echo "$1"; return 0; fi
    if [[ -f "$ROOT/$1" ]]; then echo "$ROOT/$1"; return 0; fi
    return 1
  fi

  # Search common places relative to this script folder
  for c in \
    "$ROOT/data/tasks.csv" \
    "$ROOT/tasks.csv" \
    "$ROOT/../data/tasks.csv" \
    "$ROOT/../tasks.csv" \
    "$HOME/Desktop/riotemail/data/tasks.csv" \
    "$HOME/Desktop/riotemail/tasks.csv" \
    "$HOME/Desktop/riotemail/riot-email-update-safari-mac/data/tasks.csv" \
    "$HOME/Desktop/riotemail/riot-email-update-safari-mac/tasks.csv"
  do
    if [[ -f "$c" ]] && csv_has_rows "$c"; then
      echo "$c"
      return 0
    fi
  done

  # Last resort: any tasks.csv under Desktop/riotemail with data rows
  if [[ -d "$HOME/Desktop/riotemail" ]]; then
    while IFS= read -r c; do
      if csv_has_rows "$c"; then
        echo "$c"
        return 0
      fi
    done < <(find "$HOME/Desktop/riotemail" -type f -name 'tasks.csv' 2>/dev/null | head -20)
  fi
  return 1
}

echo "== Riot Safari launcher =="
echo "  script folder: $ROOT"
echo "  looking for tasks.csv ..."

CSV_CANDIDATE=""
EXPLICIT_ARG=""
if [[ $# -ge 1 && "$1" != --* ]]; then
  EXPLICIT_ARG="$1"
  if CSV_CANDIDATE="$(find_tasks_csv "$1")"; then
    shift
  else
    echo "ERROR: CSV not found: $1" >&2
    echo "  Expected something like:" >&2
    echo "    $ROOT/data/tasks.csv" >&2
    echo "  Your folder is: $ROOT" >&2
    echo "  Put tasks.csv here:  $ROOT/data/tasks.csv" >&2
    exit 2
  fi
elif CSV_CANDIDATE="$(find_tasks_csv)"; then
  :
else
  echo "ERROR: could not find a tasks.csv with account rows." >&2
  echo
  echo "  Script folder: $ROOT"
  echo "  Put your CSV at ONE of these paths:" >&2
  echo "    $ROOT/data/tasks.csv" >&2
  echo "    $ROOT/tasks.csv" >&2
  echo "    $HOME/Desktop/riotemail/data/tasks.csv" >&2
  echo
  echo "  Folder layout should look like:" >&2
  echo "    Desktop/riotemail/" >&2
  echo "      data/" >&2
  echo "        tasks.csv          ← your accounts here" >&2
  echo "      run_safari_mac.sh" >&2
  echo "      safari_riot_email.applescript" >&2
  echo "      RUN_ME.command" >&2
  echo
  echo "  Then in Terminal:" >&2
  echo "    cd $ROOT" >&2
  echo "    chmod +x *.sh *.command fetch_riot_imap_code.py" >&2
  echo "    ./run_safari_mac.sh" >&2
  exit 2
fi

if ! csv_has_rows "$CSV_CANDIDATE"; then
  echo "ERROR: $CSV_CANDIDATE has no account rows (only header or empty)." >&2
  echo "  Add lines like:" >&2
  echo "  riotuser,password,imap@icloud.com,app-pass,new@icloud.com,imap.mail.me.com,993,," >&2
  exit 2
fi

echo "  using CSV: $CSV_CANDIDATE"
echo "  (usernames come from this file — no username dialog)"
echo
exec python3 "$ROOT/run_safari_batch.py" "$CSV_CANDIDATE" "$@"
