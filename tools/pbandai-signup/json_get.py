#!/usr/bin/env python3
"""Read a dotted JSON key from config.json for AppleScript."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("key")
    parser.add_argument("--default", default="")
    args = parser.parse_args()

    data = json.loads(args.config.read_text(encoding="utf-8"))
    cur = data
    try:
        for part in args.key.split("."):
            cur = cur[part]
    except (KeyError, TypeError):
        print(args.default)
        return

    if isinstance(cur, bool):
        print("true" if cur else "false")
    elif cur is None:
        print(args.default)
    else:
        print(cur)


if __name__ == "__main__":
    main()
