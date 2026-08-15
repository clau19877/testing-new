#!/usr/bin/env python3
"""
One-click launcher for the Riot email-update batch runner (manual captcha).

Mirrors launch.sh, with Windows support for RiotEmailUpdate.exe / launch.bat.

What it does:
  1. Sets up the Python venv + browser on first run
  2. Loads .env (proxies, optional settings)
  3. On headless Linux VMs, starts Xvfb + VNC + noVNC (:6080)
  4. Runs every row in data/tasks.csv, pausing for you to solve each captcha

Usage:
  python launch.py                 # runs data/tasks.csv
  python launch.py my-tasks.csv    # runs a specific CSV
  python launch.py --dry-run       # just validate the CSV
  RiotEmailUpdate.exe              # Windows one-click (same args)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def _venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _find_system_python() -> str:
    if os.name == "nt":
        # `py -3` is the preferred Windows launcher.
        try:
            r = subprocess.run(
                ["py", "-3", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(ROOT),
            )
            exe = (r.stdout or "").strip()
            if r.returncode == 0 and exe and Path(exe).is_file():
                return exe
        except FileNotFoundError:
            pass
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(
        "Python 3 was not found.\n"
        "  Windows: install from https://www.python.org/downloads/\n"
        "           (check 'Add python.exe to PATH'), then double-click again.\n"
        "  macOS/Linux: install python3 and re-run."
    )


def _run(cmd: list[str], *, check: bool = True, quiet: bool = False) -> int:
    kwargs: dict = {"cwd": str(ROOT)}
    if quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    r = subprocess.run(cmd, **kwargs)
    if check and r.returncode != 0:
        raise SystemExit(r.returncode)
    return r.returncode


def ensure_venv() -> Path:
    py = _venv_python()
    if py.is_file():
        return py

    print("[setup] creating virtualenv…", flush=True)
    system_py = _find_system_python()
    _run([system_py, "-m", "venv", str(ROOT / ".venv")])

    pip_py = _venv_python()
    print("[setup] upgrading pip…", flush=True)
    _run([str(pip_py), "-m", "pip", "install", "--upgrade", "pip"], quiet=True)

    req = ROOT / "requirements.txt"
    print("[setup] installing requirements (this runs once)…", flush=True)
    _run([str(pip_py), "-m", "pip", "install", "-r", str(req)])

    print("[setup] installing browser…", flush=True)
    if _run([str(pip_py), "-m", "patchright", "install", "chromium"], check=False, quiet=True) != 0:
        _run([str(pip_py), "-m", "playwright", "install", "chromium"], check=False, quiet=True)
    return pip_py


def ensure_tasks_csv(tasks_csv: Path) -> bool:
    """Return True if the CSV is ready to run; False if we just created a template."""
    if tasks_csv.is_file():
        return True

    example = ROOT / "data" / "tasks.csv.example"
    default = ROOT / "data" / "tasks.csv"
    if example.is_file() and tasks_csv.resolve() == default.resolve():
        default.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, default)
        print()
        print("  Created data/tasks.csv from the template.")
        print("  → Fill in your account rows (login, password, imap email,")
        print("    app password, new email), save the file, then run again.")
        print()
        if os.name == "nt":
            try:
                os.startfile(str(default))  # type: ignore[attr-defined]
                print("  Opened data/tasks.csv in your default editor.")
            except OSError:
                pass
        return False

    raise SystemExit(f"Task file not found: {tasks_csv}")


def ensure_display() -> None:
    if os.name == "nt" or sys.platform == "darwin":
        return

    display = os.environ.get("DISPLAY", "")
    if display and shutil.which("xdpyinfo"):
        if subprocess.run(["xdpyinfo"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            print(f"[display] using existing DISPLAY={display}", flush=True)
            return

    script = ROOT / "start_manual_session.sh"
    if not script.is_file():
        print("[display] no usable display and start_manual_session.sh missing", flush=True)
        return

    print("[display] no usable display — starting Xvfb + VNC + noVNC…", flush=True)
    subprocess.run(["bash", str(script)], cwd=str(ROOT), check=False)
    os.environ["DISPLAY"] = f":{os.environ.get('DISPLAY_NUM', '99')}"
    port = os.environ.get("NOVNC_PORT", "6080")
    print(f"[display] open http://<this-host>:{port}/vnc.html to watch/solve", flush=True)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    os.chdir(ROOT)

    csv_arg = ""
    extra: list[str] = []
    want_headless = False
    for a in argv:
        if a in ("--headless", "--no-headed"):
            want_headless = True
            extra.append(a)
        elif a.startswith("--"):
            extra.append(a)
        else:
            csv_arg = a

    tasks_csv = Path(csv_arg) if csv_arg else ROOT / "data" / "tasks.csv"
    if not tasks_csv.is_absolute():
        tasks_csv = (ROOT / tasks_csv).resolve()

    print("== Riot email-update launcher ==", flush=True)

    py = ensure_venv()
    _load_dotenv(ROOT / ".env")

    if not ensure_tasks_csv(tasks_csv):
        return 0

    # In-bot AI: OCR/CV + YesCaptcha/2Captcha Coordinates inside Playwright.
    os.environ.setdefault("CAPTCHA_PROVIDER", "vision")
    os.environ.setdefault("VISION_BACKEND", "hybrid")
    if want_headless or (os.getenv("HEADED") or "").strip().lower() in {
        "0", "false", "no", "off", "headless",
    }:
        os.environ["HEADED"] = "false"
        print("[display] headless mode — no browser window", flush=True)
    else:
        os.environ.setdefault("HEADED", "true")
        ensure_display()

    captcha = (os.getenv("CAPTCHA_PROVIDER") or "vision").strip().lower()
    print(
        f"[captcha] in-bot AI provider={captcha} "
        f"VISION_BACKEND={os.getenv('VISION_BACKEND') or 'hybrid'}",
        flush=True,
    )
    if captcha in ("vision", "hybrid") and not (
        os.getenv("TWOCAPTCHA_API_KEY") or os.getenv("TWO_CAPTCHA_API_KEY")
    ):
        print(
            "[warn] TWOCAPTCHA_API_KEY missing — hybrid vision works best with it "
            "for Coordinates fallback when local CV misses.",
            flush=True,
        )

    print(f"[run] starting tasks from {tasks_csv}", flush=True)
    cmd = [str(py), str(ROOT / "run_tasks.py"), str(tasks_csv), *extra]
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
