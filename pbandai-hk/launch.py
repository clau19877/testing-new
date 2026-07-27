#!/usr/bin/env python3
"""Python fallback launcher (used when the .exe/.bin is absent)."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def ensure_setup() -> Path:
    os.chdir(ROOT)
    py = sys.executable
    vp = venv_python()
    if not vp.exists():
        print("Creating virtual environment (.venv)...")
        run([py, "-m", "venv", str(ROOT / ".venv")])
    print("Installing/updating dependencies...")
    run([str(vp), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(vp), "-m", "pip", "install", "-r", "requirements.txt"])
    env = ROOT / ".env"
    example = ROOT / ".env.example"
    if not env.exists():
        print("Creating .env from .env.example ...")
        shutil.copyfile(example, env)
        print(f"Edit {env} and set PRODUCT_LINKS, then re-run.")
    return vp


def run_bot(vp: Path, *args: str) -> int:
    cmd = [str(vp), str(ROOT / "web_shopping_bot_hk.py"), *args]
    print("Launching:", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def menu(vp: Path) -> int:
    while True:
        print(
            """
What do you want to do?
  [1] Start monitor loop (recommended)
  [2] Run one scan
  [3] Check a direct product link
  [4] Open .env for editing
  [5] Re-run setup (deps)
  [6] List sessions
  [7] Login / create session (multi + proxy)
  [8] Login all task.csv (parallel + random proxies)
  [9] Diagnose product cart eligibility (detailed log)
  [Q] Quit
"""
        )
        choice = input("> ").strip().lower()
        if choice in {"1", ""}:
            return run_bot(vp, "loop")
        if choice == "2":
            return run_bot(vp, "once")
        if choice == "3":
            link = input("Paste product URL or code: ").strip()
            if not link:
                print("No link provided.")
                continue
            run_bot(vp, "check", link)
        elif choice == "4":
            open_env()
        elif choice == "5":
            ensure_setup()
            print("Dependencies reinstalled.")
        elif choice == "6":
            run_bot(vp, "sessions")
        elif choice == "7":
            name = input("Session name (e.g. acc1): ").strip() or "acc1"
            proxy = input("Proxy URL (blank for none): ").strip()
            args = ["login", "--name", name, "--force"]
            if proxy:
                args.extend(["--proxy", proxy])
            run_bot(vp, *args)
        elif choice == "8":
            created_csv = False
            for src_name, dst_name in (
                ("task.example.csv", "task.csv"),
                ("proxy.example.csv", "proxy.csv"),
            ):
                src = ROOT / src_name
                dst = ROOT / dst_name
                if src.exists() and not dst.exists():
                    shutil.copyfile(src, dst)
                    print(f"Created {dst_name} from {src_name}.")
                    created_csv = True
            if created_csv:
                print("Edit task.csv (login/password) and proxy.csv, then choose [8] again.")
                print("Proxy format: host:port:user:pass   OR   http://user:pass@host:port")
                continue
            if not (ROOT / "task.csv").exists() or not (ROOT / "proxy.csv").exists():
                print("Missing task.csv and/or proxy.csv. Create them first.")
                continue
            force = input("Force re-login even if cookies exist? [y/N]: ").strip().lower()
            args = ["tasks"]
            if force in {"y", "yes", "1"}:
                args.append("--force")
            run_bot(vp, *args)
        elif choice == "9":
            link = input("Paste product URL or code to diagnose: ").strip()
            if not link:
                print("No link provided.")
                continue
            run_bot(vp, "diagnose", link)
            print("Also see logs/pbandai_hk.log and logs/diagnostics/")
        elif choice in {"q", "quit", "exit"}:
            return 0
        else:
            print("Unknown option.")


def open_env() -> None:
    env = ROOT / ".env"
    print(f"Opening {env} ...")
    try:
        if os.name == "nt":
            os.startfile(str(env))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(env)])
        else:
            subprocess.Popen(["xdg-open", str(env)])
        print("Edit and save the file, then come back here.")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not open editor automatically: {exc}")
        print(f"Please edit this file manually:\n  {env}")


def main() -> int:
    parser = argparse.ArgumentParser(description="P-Bandai HK setup & launch")
    parser.add_argument("--one-click", "-y", action="store_true", help="setup + start loop")
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--check", metavar="URL")
    args = parser.parse_args()

    print("========================================")
    print(" P-Bandai HK - Setup & Launch")
    print("========================================")
    print("App folder:", ROOT)

    vp = ensure_setup()
    print("Setup complete.")
    if args.setup_only:
        return 0
    if args.check:
        return run_bot(vp, "check", args.check)
    if args.once:
        return run_bot(vp, "once")
    if args.loop or args.one_click:
        return run_bot(vp, "loop")
    return menu(vp)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print("ERROR:", exc, file=sys.stderr)
        if os.name == "nt":
            input("Press Enter to close...")
        raise SystemExit(1)
