#!/bin/bash
# Find a tasks.csv (or toolkit root) for the Safari Riot toolkit.
#
# Usage:
#   find_tasks_csv.sh [preferred_toolkit_root]
#   find_tasks_csv.sh --toolkit-root [preferred_toolkit_root]
#
# CSV preference:
#   1) preferred root / TOOL_DIR  (data/tasks.csv or tasks.csv)
#   2) Desktop/Downloads *safari-mac* toolkits (alphabetically last, e.g. " 3")
#   3) any other toolkit folder with run_safari_batch.py
#
# --toolkit-root prints the modern toolkit directory (prefers *safari-mac*).
set -euo pipefail

MODE="csv"
PREFERRED="${TOOL_DIR:-}"
for arg in "$@"; do
  if [[ "$arg" == "--toolkit-root" ]]; then
    MODE="toolkit"
  elif [[ "$arg" != --* ]]; then
    PREFERRED="$arg"
  fi
done

csv_has_rows() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  awk 'NR>1 && NF && $0 !~ /^#/ {found=1; exit} END{exit !found}' "$f"
}

toolkit_root_for_csv() {
  local f="$1" d root
  d=$(dirname "$f")
  case $(basename "$d") in
    data) root=$(dirname "$d") ;;
    *) root=$d ;;
  esac
  printf '%s' "$root"
}

is_toolkit() {
  local root="$1"
  [[ -f "$root/run_safari_batch.py" ]] || return 1
  [[ -f "$root/safari_riot_email.applescript" ]] || return 1
  return 0
}

is_modern_name() {
  local base="$1"
  [[ "$base" == *safari-mac* || "$base" == *riot-email-update* ]]
}

find_toolkit_root() {
  if [[ -n "$PREFERRED" ]] && is_toolkit "$PREFERRED"; then
    printf '%s' "$PREFERRED"
    return 0
  fi
  local best_safari="" best_other="" root base
  while IFS= read -r h; do
    [[ -z "$h" ]] && continue
    root=$(dirname "$h")
    is_toolkit "$root" || continue
    base=$(basename "$root")
    if is_modern_name "$base"; then
      best_safari="$root"
    else
      best_other="$root"
    fi
  done < <(find -L "${HOME}/Desktop" "${HOME}/Downloads" -type f -name find_tasks_csv.sh 2>/dev/null | sort)
  if [[ -n "$best_safari" ]]; then
    printf '%s' "$best_safari"
    return 0
  fi
  if [[ -n "$best_other" ]]; then
    printf '%s' "$best_other"
    return 0
  fi
  return 1
}

if [[ "$MODE" == "toolkit" ]]; then
  find_toolkit_root
  exit $?
fi

# 1) Preferred toolkit (the folder the user actually launched)
if [[ -n "$PREFERRED" ]]; then
  for c in "$PREFERRED/data/tasks.csv" "$PREFERRED/tasks.csv"; do
    if csv_has_rows "$c" && is_toolkit "$(toolkit_root_for_csv "$c")"; then
      printf '%s' "$c"
      exit 0
    fi
  done
fi

# 2 / 3) Scan Desktop + Downloads
best_safari=""
best_other=""
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  root="$(toolkit_root_for_csv "$f")"
  is_toolkit "$root" || continue
  csv_has_rows "$f" || continue
  base="$(basename "$root")"
  if is_modern_name "$base"; then
    best_safari="$f"
  else
    best_other="$f"
  fi
done < <(find -L "${HOME}/Desktop" "${HOME}/Downloads" -type f -name tasks.csv 2>/dev/null | sort)

if [[ -n "$best_safari" ]]; then
  printf '%s' "$best_safari"
  exit 0
fi
if [[ -n "$best_other" ]]; then
  printf '%s' "$best_other"
  exit 0
fi
exit 1
