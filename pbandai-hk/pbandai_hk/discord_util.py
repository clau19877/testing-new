"""Discord webhook helpers for click-farm cart success alerts."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest

from .logging_utils import get_logger

logger = get_logger("discord")

_WEBHOOK_FILENAMES = (
    "discord_webhook.txt",
    "DISCORD_WEBHOOK_URL.txt",
    "webhook.txt",
)


def normalize_webhook(value: str | None) -> str:
    """Strip quotes/whitespace and reject placeholder values."""
    text = (value or "").strip().strip('"').strip("'").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {
        "your_id/your_token",
        "https://discord.com/api/webhooks/your_id/your_token",
        "changeme",
        "todo",
        "none",
        "null",
    }:
        return ""
    if "YOUR_ID" in text or "YOUR_TOKEN" in text:
        return ""
    return text


def looks_like_discord_webhook(url: str) -> bool:
    u = (url or "").lower()
    return u.startswith("https://") and (
        "discord.com/api/webhooks/" in u or "discordapp.com/api/webhooks/" in u
    )


def redact_webhook(url: str) -> str:
    text = normalize_webhook(url)
    if not text:
        return "-"
    # Keep host + webhook id, hide token.
    try:
        parts = text.rstrip("/").split("/")
        # .../webhooks/<id>/<token>
        if len(parts) >= 2 and parts[-2].isdigit():
            return "/".join(parts[:-1]) + "/***"
    except Exception:  # noqa: BLE001
        pass
    return text[:48] + "…"


def _read_webhook_file(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # Allow KEY=value form inside the sidecar file.
            if line.upper().startswith("DISCORD_WEBHOOK") and "=" in line:
                line = line.split("=", 1)[1].strip()
            return normalize_webhook(line)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read webhook file %s: %s", path, exc)
    return ""


def webhook_search_dirs(extra: Optional[List[Path]] = None) -> List[Path]:
    dirs: List[Path] = []
    for p in [
        Path.cwd(),
        Path(__file__).resolve().parent.parent,  # pbandai-hk/
        *(extra or []),
    ]:
        try:
            rp = p.resolve()
        except Exception:  # noqa: BLE001
            rp = p
        if rp not in dirs:
            dirs.append(rp)
    return dirs


def resolve_discord_webhook(
    *,
    config_value: str = "",
    search_dirs: Optional[List[Path]] = None,
    reload_env: bool = True,
) -> Tuple[str, str]:
    """Resolve webhook URL from env / config / sidecar files.

    Returns (url, source_label). source_label is empty when unset.
    """
    # 1) Explicit config (already loaded from .env)
    cfg = normalize_webhook(config_value)
    if cfg:
        return cfg, "config"

    # 2) Live process env (aliases)
    if reload_env:
        for key in ("DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK", "DISCORD_HOOK"):
            val = normalize_webhook(os.getenv(key))
            if val:
                return val, f"env:{key}"

    # 3) Sidecar text files (survive .env reset)
    for folder in webhook_search_dirs(search_dirs):
        for name in _WEBHOOK_FILENAMES:
            path = folder / name
            val = _read_webhook_file(path)
            if val:
                return val, f"file:{path.name}"

    return "", ""


def write_webhook_sidecar(url: str, folder: Path) -> Optional[Path]:
    """Persist webhook to discord_webhook.txt so .env resets don't wipe it."""
    cleaned = normalize_webhook(url)
    if not cleaned:
        return None
    path = folder / "discord_webhook.txt"
    try:
        path.write_text(cleaned + "\n", encoding="utf-8")
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not write %s: %s", path, exc)
        return None


def append_cart_success(
    *,
    folder: Path,
    instance: str,
    product_code: str,
    payment_url: str,
    note: str,
    product_url: str = "",
) -> Path:
    """Always persist cart success locally (even when Discord is off)."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"{stamp}\t{instance}\t{product_code}\t{payment_url}\t{note}\t{product_url}\n"
    )
    log_path = folder / "cart_successes.log"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    last = folder / "last_payment_url.txt"
    last.write_text(
        f"{payment_url}\n"
        f"instance={instance}\n"
        f"product={product_code}\n"
        f"note={note}\n"
        f"at={stamp}\n",
        encoding="utf-8",
    )
    return log_path


def send_discord_webhook(
    webhook: str,
    *,
    content: str,
    embeds: Optional[List[Dict[str, Any]]] = None,
    retries: int = 3,
    timeout: float = 15.0,
) -> Tuple[bool, str]:
    """POST to Discord webhook with short retries. Returns (ok, detail)."""
    url = normalize_webhook(webhook)
    if not url:
        return False, "webhook empty"
    if not looks_like_discord_webhook(url):
        return False, f"webhook URL does not look like Discord: {redact_webhook(url)}"

    payload: Dict[str, Any] = {"content": content[:1900]}
    if embeds:
        payload["embeds"] = embeds
    data = json.dumps(payload).encode("utf-8")
    last_err = ""
    for attempt in range(1, max(1, retries) + 1):
        try:
            req = urlrequest.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "pbandai-hk-bot",
                },
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 0) or 0
                if 200 <= int(status) < 300:
                    return True, f"status={status}"
                last_err = f"status={status}"
        except urlerror.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            last_err = f"HTTP {exc.code}: {body or exc.reason}"
            # 429 — back off; 4xx other — don't spam forever
            if exc.code == 429:
                time.sleep(1.5 * attempt)
            elif 400 <= exc.code < 500 and exc.code != 429:
                return False, last_err
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            time.sleep(0.6 * attempt)
    return False, last_err or "unknown error"
