"""
2Captcha *click* hybrid for in-browser hCaptcha.

Why not token API?
  Riot enterprise rejects out-of-band P1_ tokens (invalid_request) even when
  2Captcha/NoneCap mint them — solve IP / session / rqdata binding fails.

Hybrid approach:
  1. Screenshot the live challenge iframe (same browser, same residential IP)
  2. Send image + instruction text to 2Captcha CoordinatesTask (human workers)
  3. Click / drag those coordinates inside the iframe
  4. hCaptcha mints the token in-session → Riot can accept it

This is the practical "work around 2cap" path. A fully local trained model is
a longer effort; see collect_training_sample() for labeling via this oracle.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from captcha import CaptchaSolverError

TWOCAPTCHA_CREATE = "https://api.2captcha.com/createTask"
TWOCAPTCHA_RESULT = "https://api.2captcha.com/getTaskResult"

DEBUG_DIR = Path(__file__).resolve().parent / "debug"
SAMPLES_DIR = DEBUG_DIR / "training_samples"


def _log(msg: str) -> None:
    print(f"[2cap-click] {msg}", flush=True)


def _api_key() -> str:
    key = (
        os.getenv("TWOCAPTCHA_API_KEY")
        or os.getenv("TWO_CAPTCHA_API_KEY")
        or os.getenv("2CAPTCHA_API_KEY")
        or ""
    ).strip()
    if not key:
        raise CaptchaSolverError("TWOCAPTCHA_API_KEY not set")
    return key


def extract_instruction(screenshot: Path) -> str:
    """Best-effort OCR of the challenge prompt for the worker comment."""
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(screenshot)
        w, h = img.size
        # Prompt banner + requirement strip (1x F / 1x Z) sit in the top ~38%
        top = img.crop((0, 0, w, int(h * 0.38)))
        text = pytesseract.image_to_string(top).strip()
        if text:
            cleaned = re.sub(r"\s+", " ", text)[:280]
            # Prefer keeping Nx Letter tokens for workers
            return cleaned
    except Exception as exc:
        _log(f"instruction OCR failed: {exc}")
    return (
        "Solve this hCaptcha challenge. "
        "For letter grids click the required letters the listed number of times. "
        "For drag challenges drag the object onto the target."
    )


def letter_grid_click_hint(screenshot: Path) -> tuple[str, int]:
    """
    Build a stronger CoordinatesTask comment for letter grids and estimate
    minimum clicks from '1x' / '2x' tokens in the requirement strip.
    """
    instruction = extract_instruction(screenshot)
    times = [int(n) for n in re.findall(r"(\d)\s*[xX]", instruction)]
    min_clicks = max(1, sum(times) if times else 2)
    comment = (
        f"{instruction}. "
        "Click EVERY required letter tile the exact number of times shown "
        "(e.g. 1x F means click F once). Do not click Skip or Verify."
    )
    return comment, min_clicks



def _image_to_b64_under_limit(path: Path, limit_kb: int = 95) -> str:
    """
    2Captcha CoordinatesTask rejects bodies over ~100 kB.
    Re-encode as JPEG and shrink until under the limit.
    """
    from io import BytesIO

    from PIL import Image

    img = Image.open(path).convert("RGB")
    # Challenge widgets are ~520x570; full-page shots need a hard downscale
    w, h = img.size
    if w > 700 or h > 800:
        img = img.resize((min(w, 520), int(h * min(w, 520) / w)), Image.Resampling.LANCZOS)

    quality = 85
    raw = b""
    for _ in range(12):
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        raw = buf.getvalue()
        if len(raw) <= limit_kb * 1024:
            break
        if quality > 40:
            quality -= 10
        else:
            nw = int(img.size[0] * 0.85)
            nh = int(img.size[1] * 0.85)
            if nw < 200:
                break
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    _log(f"upload image {len(raw)} bytes (q={quality}, size={img.size})")
    return base64.b64encode(raw).decode()


def solve_coordinates(
    screenshot: Path,
    *,
    comment: str | None = None,
    min_clicks: int = 1,
    max_clicks: int = 8,
    timeout: float = 120.0,
) -> list[dict[str, int]]:
    """
    Send challenge screenshot to 2Captcha CoordinatesTask.
    Returns list of {x,y} in screenshot pixel coords (top-left origin).
    """
    key = _api_key()
    comment = (comment or extract_instruction(screenshot)).strip()
    b64 = _image_to_b64_under_limit(screenshot)

    task: dict[str, Any] = {
        "type": "CoordinatesTask",
        "body": b64,
        "comment": comment,
        "minClicks": min_clicks,
        "maxClicks": max_clicks,
    }
    _log(f"CoordinatesTask comment={comment[:80]!r}…")
    create = requests.post(
        TWOCAPTCHA_CREATE,
        json={"clientKey": key, "task": task},
        timeout=60,
    )
    body = create.json()
    if body.get("errorId"):
        raise CaptchaSolverError(f"2Captcha Coordinates create error: {body}")
    task_id = body.get("taskId")
    if task_id is None:
        raise CaptchaSolverError(f"2Captcha Coordinates no taskId: {body}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        resp = requests.post(
            TWOCAPTCHA_RESULT,
            json={"clientKey": key, "taskId": task_id},
            timeout=60,
        )
        data = resp.json()
        if data.get("errorId"):
            raise CaptchaSolverError(f"2Captcha Coordinates result error: {data}")
        if data.get("status") != "ready":
            _log(f"status={data.get('status') or 'processing'}…")
            continue
        coords = (data.get("solution") or {}).get("coordinates") or []
        out = [{"x": int(c["x"]), "y": int(c["y"])} for c in coords]
        _log(f"got {len(out)} clicks: {out}")
        return out
    raise CaptchaSolverError(f"2Captcha Coordinates timed out after {timeout:.0f}s")


def collect_training_sample(
    screenshot: Path,
    *,
    instruction: str,
    clicks: list[dict[str, int]],
    drags: list[dict[str, int]] | None = None,
    meta: dict[str, Any] | None = None,
) -> Path:
    """
    Save labeled sample for future local-model training.
    Layout: debug/training_samples/<ts>/{challenge.png, label.json}
    """
    from PIL import Image

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = str(int(time.time()))
    folder = SAMPLES_DIR / stamp
    folder.mkdir(exist_ok=True)
    dest = folder / "challenge.png"
    dest.write_bytes(screenshot.read_bytes())
    with Image.open(dest) as im:
        size = list(im.size)
    label = {
        "instruction": instruction,
        "clicks": clicks,
        "drags": drags or [],
        "image": "challenge.png",
        "image_size": size,
        "meta": meta or {},
        "source": "twocaptcha_coordinates",
        "created": time.time(),
    }
    (folder / "label.json").write_text(json.dumps(label, indent=2))
    _log(f"saved training sample → {folder}")
    return folder


def plan_twocaptcha_clicks(screenshot: Path):
    """Return a VisionPlan built from 2Captcha coordinate workers."""
    from vision_captcha import ClickTarget, DragTarget, VisionPlan

    instruction = extract_instruction(screenshot)
    lower = instruction.lower()

    # Drag challenges: ask worker for two points (source then destination)
    if "drag" in lower:
        coords = solve_coordinates(
            screenshot,
            comment=(
                f"{instruction}. "
                "Click FIRST on the object to drag (source), "
                "THEN on the destination target."
            ),
            min_clicks=2,
            max_clicks=2,
        )
        if len(coords) < 2:
            return VisionPlan(
                instruction=instruction,
                clicks=[],
                backend="twocaptcha",
                notes=f"drag needs 2 points, got {len(coords)}",
            )
        drags = [
            DragTarget(
                x1=coords[0]["x"],
                y1=coords[0]["y"],
                x2=coords[1]["x"],
                y2=coords[1]["y"],
                label="2cap-drag",
            )
        ]
        try:
            collect_training_sample(
                screenshot,
                instruction=instruction,
                clicks=[],
                drags=[d.__dict__ for d in drags],
            )
        except Exception as exc:
            _log(f"training sample save skipped: {exc}")
        return VisionPlan(
            instruction=instruction,
            clicks=[],
            drags=drags,
            backend="twocaptcha",
            notes="2Captcha Coordinates drag (src→dst)",
        )

    if "letter" in lower or "click each" in lower:
        comment, min_clicks = letter_grid_click_hint(screenshot)
    else:
        comment = instruction or (
            "Click the required tiles/letters for this hCaptcha challenge."
        )
        min_clicks = 1
    coords = solve_coordinates(
        screenshot,
        comment=comment,
        min_clicks=min_clicks,
        max_clicks=8,
    )
    clicks = [
        ClickTarget(x=c["x"], y=c["y"], label=f"2cap#{i+1}", times=1)
        for i, c in enumerate(coords)
    ]
    try:
        collect_training_sample(
            screenshot,
            instruction=instruction,
            clicks=[c.__dict__ for c in clicks],
        )
    except Exception as exc:
        _log(f"training sample save skipped: {exc}")
    return VisionPlan(
        instruction=instruction,
        clicks=clicks,
        backend="twocaptcha",
        notes=f"2Captcha Coordinates ({len(clicks)} clicks)",
    )
