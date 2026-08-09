#!/bin/bash
# Find a tasks.csv next to run_safari_batch.py (Desktop "… safari-mac 3", etc.).
# Prints one path; prefer alphabetically last match so " 3" wins over older copies.
set -euo pipefail
found=""
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  d=$(dirname "$f")
  case $(basename "$d") in
    data) root=$(dirname "$d") ;;
    *) root=$d ;;
  esac
  [[ -f "$root/run_safari_batch.py" ]] || continue
  awk 'NR>1 && NF && $0 !~ /^#/ {found=1; exit} END{exit !found}' "$f" || continue
  found=$f
done < <(find "${HOME}/Desktop" "${HOME}/Downloads" -type f -name tasks.csv 2>/dev/null | sort)
printf '%s' "$found"
