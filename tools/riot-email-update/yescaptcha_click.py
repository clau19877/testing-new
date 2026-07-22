"""
YesCaptcha HCaptchaClassification hybrid for in-browser hCaptcha.

Returns click / drag coordinates (not a token). Apply them in the same
Playwright session + residential proxy so the widget mints the token in-session.

Docs: https://yescaptcha.atlassian.net/wiki/spaces/YESCAPTCHA/pages/909246465
"""

from __future__ import annotations

import base64
import os
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import requests

from captcha import CaptchaSolverError

YESCAPTCHA_CREATE = os.getenv(
    "YESCAPTCHA_CREATE_URL", "https://api.yescaptcha.com/createTask"
)
YESCAPTCHA_RESULT = os.getenv(
    "YESCAPTCHA_RESULT_URL", "https://api.yescaptcha.com/getTaskResult"
)
YESCAPTCHA_BALANCE = os.getenv(
    "YESCAPTCHA_BALANCE_URL", "https://api.yescaptcha.com/getBalance"
)


def _log(msg: str) -> None:
    print(f"[yescaptcha] {msg}", flush=True)


def _api_key() -> str:
    key = (
        os.getenv("YESCAPTCHA_API_KEY")
        or os.getenv("YES_CAPTCHA_API_KEY")
        or ""
    ).strip()
    if not key:
        raise CaptchaSolverError("YESCAPTCHA_API_KEY not set")
    return key


def get_balance() -> float | None:
    try:
        r = requests.post(
            YESCAPTCHA_BALANCE,
            json={"clientKey": _api_key()},
            timeout=30,
        )
        data = r.json()
        if data.get("errorId"):
            _log(f"balance error: {data}")
            return None
        return float(data.get("balance", 0))
    except Exception as exc:
        _log(f"balance check failed: {exc}")
        return None


def _image_to_jpeg_b64(path: Path, *, max_side: int = 900) -> tuple[str, tuple[int, int]]:
    """Return (raw_base64_without_prefix, (orig_w, orig_h))."""
    from PIL import Image

    img = Image.open(path).convert("RGB")
    orig = img.size
    w, h = orig
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buf.getvalue()).decode(), orig


def extract_question(screenshot: Path) -> str:
    try:
        from twocaptcha_click import extract_instruction

        return extract_instruction(screenshot)
    except Exception:
        return "Solve this hCaptcha challenge"


def create_classification_task(
    screenshot: Path,
    *,
    question: str | None = None,
) -> str:
    key = _api_key()
    q = (question or extract_question(screenshot)).strip()
    b64, _orig = _image_to_jpeg_b64(screenshot)
    # Screenshot mode (drag / point / new styles): single JPEG in queries.
    task: dict[str, Any] = {
        "type": "HCaptchaClassification",
        "queries": [b64],
        "question": q,
        "anchors": [],
    }
    _log(f"createTask question={q[:90]!r}")
    resp = requests.post(
        YESCAPTCHA_CREATE,
        json={"clientKey": key, "task": task},
        timeout=60,
    )
    data = resp.json()
    if data.get("errorId"):
        raise CaptchaSolverError(f"YesCaptcha createTask: {data}")
    task_id = data.get("taskId")
    if not task_id:
        raise CaptchaSolverError(f"YesCaptcha missing taskId: {data}")
    return str(task_id)


def poll_result(task_id: str, *, timeout: float = 90.0) -> dict[str, Any]:
    key = _api_key()
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.post(
            YESCAPTCHA_RESULT,
            json={"clientKey": key, "taskId": task_id},
            timeout=60,
        )
        data = resp.json()
        if data.get("errorId"):
            raise CaptchaSolverError(f"YesCaptcha getTaskResult: {data}")
        status = (data.get("status") or "").lower()
        if status == "ready":
            sol = data.get("solution") or {}
            if not isinstance(sol, dict):
                raise CaptchaSolverError(f"YesCaptcha bad solution: {data}")
            return sol
        if status in ("processing", "idle", ""):
            _log("status=processing…")
            time.sleep(3)
            continue
        raise CaptchaSolverError(f"YesCaptcha unexpected status: {data}")
    raise CaptchaSolverError(f"YesCaptcha timeout waiting for {task_id}")


def _scale_xy(
    x: float, y: float, *, upload_size: tuple[int, int], orig_size: tuple[int, int]
) -> tuple[int, int]:
    uw, uh = upload_size
    ow, oh = orig_size
    if uw <= 0 or uh <= 0:
        return int(x), int(y)
    return int(x * ow / uw), int(y * oh / uh)


def _parse_box_clicks(
    box: Any,
    *,
    upload_size: tuple[int, int],
    orig_size: tuple[int, int],
) -> list[dict[str, int]]:
    """Parse flat [x,y,x,y…] or nested structures into click points."""
    clicks: list[dict[str, int]] = []
    if not box:
        return clicks
    # Drag objects: [{start:[x,y], end:[x,y]}, ...]
    if isinstance(box, list) and box and isinstance(box[0], dict):
        return clicks  # handled by drag parser
    if isinstance(box, list):
        nums: list[float] = []
        for item in box:
            if isinstance(item, (int, float, str)):
                try:
                    nums.append(float(item))
                except ValueError:
                    continue
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                sx, sy = _scale_xy(
                    float(item[0]), float(item[1]), upload_size=upload_size, orig_size=orig_size
                )
                clicks.append({"x": sx, "y": sy})
        for i in range(0, len(nums) - 1, 2):
            sx, sy = _scale_xy(
                nums[i], nums[i + 1], upload_size=upload_size, orig_size=orig_size
            )
            clicks.append({"x": sx, "y": sy})
    return clicks


def _parse_box_drags(
    box: Any,
    *,
    upload_size: tuple[int, int],
    orig_size: tuple[int, int],
) -> list[dict[str, int]]:
    drags: list[dict[str, int]] = []
    if not isinstance(box, list):
        return drags
    for item in box:
        if not isinstance(item, dict):
            continue
        start = item.get("start") or item.get("Start")
        end = item.get("end") or item.get("End")
        if not (isinstance(start, (list, tuple)) and isinstance(end, (list, tuple))):
            continue
        if len(start) < 2 or len(end) < 2:
            continue
        x1, y1 = _scale_xy(
            float(start[0]), float(start[1]), upload_size=upload_size, orig_size=orig_size
        )
        x2, y2 = _scale_xy(
            float(end[0]), float(end[1]), upload_size=upload_size, orig_size=orig_size
        )
        drags.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return drags


def plan_yescaptcha_clicks(screenshot: Path, extra_comment: str | None = None):
    """Return a VisionPlan from YesCaptcha HCaptchaClassification."""
    from PIL import Image

    from vision_captcha import ClickTarget, DragTarget, VisionPlan

    question = extract_question(screenshot)
    if extra_comment:
        question = f"{question}. {extra_comment.strip()}"

    b64, _ = _image_to_jpeg_b64(screenshot)
    up = Image.open(BytesIO(base64.b64decode(b64)))
    upload_size = up.size
    orig_size = Image.open(screenshot).size

    key = _api_key()
    task = {
        "type": "HCaptchaClassification",
        "queries": [b64],
        "question": question,
        "anchors": [],
    }
    _log(f"createTask question={question[:90]!r} upload={upload_size} orig={orig_size}")
    resp = requests.post(
        YESCAPTCHA_CREATE,
        json={"clientKey": key, "task": task},
        timeout=60,
    )
    data = resp.json()
    if data.get("errorId"):
        raise CaptchaSolverError(f"YesCaptcha createTask: {data}")
    task_id = data.get("taskId")
    if not task_id:
        raise CaptchaSolverError(f"YesCaptcha missing taskId: {data}")

    sol = poll_result(str(task_id))
    sol_type = (sol.get("type") or "").lower()
    box = sol.get("box")
    _log(f"solution type={sol_type or 'click'} box={str(box)[:120]}")

    drags_raw = _parse_box_drags(box, upload_size=upload_size, orig_size=orig_size)
    if sol_type == "drag" or drags_raw:
        drags = [
            DragTarget(
                x1=d["x1"], y1=d["y1"], x2=d["x2"], y2=d["y2"], label=f"yes-drag#{i+1}"
            )
            for i, d in enumerate(drags_raw)
        ]
        return VisionPlan(
            instruction=question,
            clicks=[],
            drags=drags,
            backend="yescaptcha",
            notes=f"YesCaptcha Classification drag ({len(drags)})",
        )

    clicks_raw = _parse_box_clicks(box, upload_size=upload_size, orig_size=orig_size)
    clicks = [
        ClickTarget(x=c["x"], y=c["y"], label=f"yes#{i+1}", times=1)
        for i, c in enumerate(clicks_raw)
    ]
    if not clicks:
        return VisionPlan(
            instruction=question,
            clicks=[],
            backend="yescaptcha",
            notes="YesCaptcha Classification empty box",
        )
    return VisionPlan(
        instruction=question,
        clicks=clicks,
        backend="yescaptcha",
        notes=f"YesCaptcha Classification ({len(clicks)} clicks)",
    )
