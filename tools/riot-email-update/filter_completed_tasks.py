#!/usr/bin/env python3
"""Remove accounts already listed in success.txt from data/tasks.csv.

Usage (on your Mac, in the toolkit folder):
  python3 filter_completed_tasks.py
  python3 filter_completed_tasks.py --tasks data/tasks.csv --success success.txt --out data/tasks.csv

By default writes data/tasks_remaining.csv and also replaces data/tasks.csv
(after saving a timestamped backup).
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_success_users(path: Path) -> set[str]:
    users: set[str] = set()
    if not path.exists():
        raise SystemExit(f"success file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # success.txt is tab-separated; first field is riot_username
        user = line.split("\t")[0].strip()
        if user:
            users.add(user)
    return users


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default=str(ROOT / "data" / "tasks.csv"))
    ap.add_argument("--success", default=str(ROOT / "success.txt"))
    ap.add_argument(
        "--out",
        default=str(ROOT / "data" / "tasks_remaining.csv"),
        help="Filtered CSV path (default: data/tasks_remaining.csv)",
    )
    ap.add_argument(
        "--replace-tasks",
        action="store_true",
        default=True,
        help="Also replace data/tasks.csv with the filtered file (default: on)",
    )
    ap.add_argument(
        "--no-replace-tasks",
        action="store_true",
        help="Do not modify data/tasks.csv",
    )
    args = ap.parse_args()

    tasks_path = Path(args.tasks).expanduser()
    success_path = Path(args.success).expanduser()
    out_path = Path(args.out).expanduser()
    replace = args.replace_tasks and not args.no_replace_tasks

    if not tasks_path.exists():
        raise SystemExit(f"tasks CSV not found: {tasks_path}")

    success_users = load_success_users(success_path)
    with tasks_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise SystemExit(f"empty CSV: {tasks_path}")

    header, body = rows[0], rows[1:]
    kept: list[list[str]] = []
    removed: list[str] = []
    for row in body:
        if not row or not str(row[0]).strip() or str(row[0]).strip().startswith("#"):
            continue
        user = str(row[0]).strip()
        if user in success_users:
            removed.append(user)
        else:
            kept.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(kept)

    print(f"success.txt accounts: {len(success_users)}")
    print(f"tasks.csv rows:       {len(body)}")
    print(f"removed:              {len(removed)}")
    print(f"remaining:            {len(kept)}")
    print(f"wrote:                {out_path}")

    if replace:
        # Prefer replacing the real tasks.csv beside the toolkit.
        target = tasks_path
        if target.resolve() != out_path.resolve():
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup = target.with_name(f"tasks.csv.bak-{stamp}")
            shutil.copy2(target, backup)
            shutil.copy2(out_path, target)
            print(f"backup:               {backup}")
            print(f"replaced:             {target}")

    if removed:
        print("removed usernames:")
        for u in removed:
            print(f"  {u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
