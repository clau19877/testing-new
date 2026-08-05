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
    ensure_env_file()
    return vp


def ensure_env_file() -> None:
    """Create .env from example, or append any missing keys from .env.example."""
    env = ROOT / ".env"
    example = ROOT / ".env.example"
    if not example.exists():
        print("WARNING: .env.example missing")
        return
    if not env.exists():
        print("Creating .env from .env.example ...")
        shutil.copyfile(example, env)
        # Prefer durable sidecar webhook if present.
        sidecar = ROOT / "discord_webhook.txt"
        if sidecar.exists():
            for raw in sidecar.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if line and not line.startswith("#"):
                    _set_env_value(env, "DISCORD_WEBHOOK_URL", line.strip('"').strip("'"))
                    print("Filled DISCORD_WEBHOOK_URL from discord_webhook.txt")
                    break
        print(f"Edit {env} then re-run.")
        return
    added = merge_missing_env_keys(example, env)
    if added:
        print(f"Updated .env with {len(added)} new key(s): {', '.join(added)}")
        print("Review .env — new drop settings may need values.")
    # If .env has empty webhook but sidecar has one, inject it.
    if not _read_env_value(env, "DISCORD_WEBHOOK_URL"):
        sidecar = ROOT / "discord_webhook.txt"
        if sidecar.exists():
            for raw in sidecar.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if line and not line.startswith("#"):
                    _set_env_value(env, "DISCORD_WEBHOOK_URL", line.strip('"').strip("'"))
                    print("Filled DISCORD_WEBHOOK_URL from discord_webhook.txt")
                    break


def merge_missing_env_keys(example: Path, env: Path) -> list[str]:
    """Append KEY= lines from example that are absent in .env. Never overwrite."""
    existing = _env_keys(env.read_text(encoding="utf-8", errors="replace"))
    example_text = example.read_text(encoding="utf-8", errors="replace")
    to_append: list[str] = []
    added_keys: list[str] = []
    for raw in example_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if not key or key in existing:
            continue
        to_append.append(raw.rstrip("\n"))
        added_keys.append(key)
        existing.add(key)
    if to_append:
        with env.open("a", encoding="utf-8") as fh:
            fh.write("\n# --- keys added from .env.example ---\n")
            fh.write("\n".join(to_append) + "\n")
    return added_keys


def _env_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def _read_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    prefix = f"{key}="
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _set_env_value(path: Path, key: str, value: str) -> None:
    """Set or replace KEY=value in an .env file (preserves other lines)."""
    if not value:
        return
    lines: list[str] = []
    found = False
    if path.exists():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(raw.rstrip("\n"))
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reset_env_from_example() -> None:
    example = ROOT / ".env.example"
    env = ROOT / ".env"
    if not example.exists():
        print(".env.example not found")
        return
    # Preserve Discord webhook across reset (also write durable sidecar).
    old_hook = _read_env_value(env, "DISCORD_WEBHOOK_URL") if env.exists() else ""
    sidecar = ROOT / "discord_webhook.txt"
    if not old_hook and sidecar.exists():
        for raw in sidecar.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                old_hook = line.strip('"').strip("'")
                break
    if env.exists():
        bak = ROOT / f".env.bak.{int(__import__('time').time())}"
        shutil.copyfile(env, bak)
        print(f"Backed up old .env -> {bak.name}")
    shutil.copyfile(example, env)
    if old_hook:
        _set_env_value(env, "DISCORD_WEBHOOK_URL", old_hook)
        try:
            sidecar.write_text(old_hook + "\n", encoding="utf-8")
        except OSError:
            pass
        print("Preserved DISCORD_WEBHOOK_URL from previous .env / discord_webhook.txt")
    print("Replaced .env with latest .env.example")
    open_env()


def run_bot(vp: Path, *args: str) -> int:
    cmd = [str(vp), str(ROOT / "web_shopping_bot_hk.py"), *args]
    print("Launching:", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def menu(vp: Path) -> int:
    while True:
        print(
            """
What do you want to do?
  [1] Start click farm / monitor loop (recommended)
  [2] Run one pass
  [3] Check a direct product link
  [4] Open .env for editing
  [0] Reset .env from latest .env.example (backup old)
  [5] Re-run setup (deps)
  [6] List sessions (legacy)
  [9] Diagnose product cart eligibility
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
        elif choice == "0":
            ans = input("Replace .env with .env.example? [y/N]: ").strip().lower()
            if ans in {"y", "yes", "1"}:
                reset_env_from_example()
        elif choice == "5":
            ensure_setup()
            print("Dependencies reinstalled.")
        elif choice == "6":
            run_bot(vp, "sessions")
        elif choice in {"7", "8"}:
            print("Login removed. Guest click farm needs no accounts.")
            print("Set CLICK_FARM=1, BROWSER_INSTANCES, DISCORD_WEBHOOK_URL in .env")
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
            print("Unknown choice.")


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
