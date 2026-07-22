"""
Multibot hCaptcha click + humanMove client.

Docs: https://multibot.cloud/en/api/
Open-source reference: multibot-solver/hcaptcha-click-solver

Requires MULTIBOT_API_KEY. Used for in-browser Canvas/Drag/Grid challenges
with Multibot-generated mouse trajectories (stronger than local Bezier alone).
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
from human_mouse import replay_path

CREATE_URL = os.getenv(
    "MULTIBOT_CREATE_URL", "https://api.multibot.in/createTask/index.php"
)
RESULT_URL = os.getenv(
    "MULTIBOT_RESULT_URL", "https://api.multibot.in/getTaskResult/index.php"
)
# Fallback cloud host used by public docs
CREATE_URL_CLOUD = "https://api.multibot.cloud/createTask"
RESULT_URL_CLOUD = "https://api.multibot.cloud/getTaskResult"


def _log(msg: str) -> None:
    print(f"[multibot] {msg}", flush=True)


def _api_key() -> str:
    key = (os.getenv("MULTIBOT_API_KEY") or os.getenv("MULTIBOT_KEY") or "").strip()
    if not key:
        raise CaptchaSolverError("MULTIBOT_API_KEY not set")
    return key


def _create_and_poll(
    task: Any,
    *,
    task_type: str,
    timeout: float = 60.0,
    poll: float = 1.0,
) -> dict[str, Any]:
    key = _api_key()
    payload = {"clientKey": key, "type": task_type, "task": task}
    last_err: Exception | None = None
    task_id = None
    for url in (CREATE_URL, CREATE_URL_CLOUD):
        try:
            r = requests.post(url, json=payload, timeout=30)
            body = r.json()
            if body.get("errorId"):
                last_err = CaptchaSolverError(f"Multibot create error: {body}")
                continue
            task_id = body.get("taskId")
            if task_id:
                break
            last_err = CaptchaSolverError(f"Multibot no taskId: {body}")
        except Exception as exc:
            last_err = exc
    if not task_id:
        raise CaptchaSolverError(f"Multibot createTask failed: {last_err}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll)
        for url in (RESULT_URL, RESULT_URL_CLOUD):
            try:
                r = requests.post(
                    url,
                    json={"clientKey": key, "taskId": task_id},
                    timeout=30,
                )
                body = r.json()
            except Exception:
                continue
            if body.get("errorId"):
                raise CaptchaSolverError(f"Multibot result error: {body}")
            status = body.get("status")
            if status == "ready":
                return body
            if status == "failed":
                raise CaptchaSolverError(f"Multibot failed: {body}")
            _log(f"status={status or 'processing'}…")
            break
    raise CaptchaSolverError(f"Multibot timed out after {timeout:.0f}s")


def request_human_move(
    route: list[list[float]],
    *,
    timeout: float = 30.0,
) -> list[tuple[float, float, float]]:
    """
    route: [[x1,y1],[x2,y2],...]
    returns [(x,y,delay_ms), ...]
    """
    task = [{"type": "move", "patch": [[float(p[0]), float(p[1])] for p in route]}]
    body = _create_and_poll(task, task_type="humanMove", timeout=timeout)
    answers = body.get("answers") or []
    out: list[tuple[float, float, float]] = []
    for segment in answers:
        if not isinstance(segment, dict):
            continue
        path = segment.get("path") or []
        for entry in path:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            delay = float(entry[2]) if len(entry) > 2 and entry[2] is not None else 0.0
            out.append((float(entry[0]), float(entry[1]), delay))
    if not out:
        raise CaptchaSolverError(f"Multibot humanMove empty: {body}")
    return out


def solve_canvas_or_drag(
    *,
    image_b64: str,
    question: str,
    request_type: str,
    examples: list[str] | None = None,
    timeout: float = 60.0,
) -> Any:
    """
    request_type: Canvas | Drag | Grid
    Returns Multibot answers payload (list/dict).
    """
    task: dict[str, Any] = {
        "request_type": request_type,
        "question": question,
        "body": image_b64,
    }
    if examples:
        task["examples"] = examples
    _log(f"hCaptchaBase64 type={request_type} q={question[:60]!r}")
    body = _create_and_poll(task, task_type="hCaptchaBase64", timeout=timeout)
    answers = body.get("answers")
    if answers is None:
        raise CaptchaSolverError(f"Multibot empty answers: {body}")
    return answers


def plan_multibot_from_screenshot(
    screenshot: Path,
    *,
    instruction: str = "",
    request_type: str | None = None,
):
    """Build a VisionPlan from Multibot Canvas/Drag classification."""
    from vision_captcha import ClickTarget, DragTarget, VisionPlan

    from PIL import Image

    img = Image.open(screenshot).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    lower = (instruction or "").lower()
    rtype = request_type
    if not rtype:
        rtype = "Drag" if "drag" in lower else "Canvas"
    answers = solve_canvas_or_drag(
        image_b64=b64, question=instruction or "Solve the captcha", request_type=rtype
    )

    clicks: list[ClickTarget] = []
    drags: list[DragTarget] = []

    # Normalize common answer shapes
    seq = answers
    if isinstance(answers, dict):
        seq = answers.get("actions") or answers.get("answers") or answers.get("box") or []

    if rtype == "Drag" and isinstance(seq, list) and seq:
        # pairs of points or {start,end}
        if isinstance(seq[0], dict) and ("start" in seq[0] or "x1" in seq[0]):
            for i, d in enumerate(seq):
                if "start" in d and "end" in d:
                    s, e = d["start"], d["end"]
                    drags.append(
                        DragTarget(
                            x1=int(s[0]), y1=int(s[1]), x2=int(e[0]), y2=int(e[1]),
                            label=f"mb-drag#{i+1}",
                        )
                    )
                elif "x1" in d:
                    drags.append(
                        DragTarget(
                            x1=int(d["x1"]), y1=int(d["y1"]),
                            x2=int(d["x2"]), y2=int(d["y2"]),
                            label=f"mb-drag#{i+1}",
                        )
                    )
        elif isinstance(seq[0], (list, tuple)) and len(seq) >= 2:
            # flat list of points → pair them
            pts = [(int(p[0]), int(p[1])) for p in seq if isinstance(p, (list, tuple))]
            for i in range(0, len(pts) - 1, 2):
                drags.append(
                    DragTarget(
                        x1=pts[i][0], y1=pts[i][1],
                        x2=pts[i + 1][0], y2=pts[i + 1][1],
                        label=f"mb-drag#{i//2+1}",
                    )
                )
    else:
        # Canvas clicks: list of [x,y] or {x,y}
        if isinstance(seq, list):
            for i, p in enumerate(seq):
                if isinstance(p, dict) and "x" in p:
                    clicks.append(ClickTarget(x=int(p["x"]), y=int(p["y"]), label=f"mb#{i+1}"))
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    clicks.append(ClickTarget(x=int(p[0]), y=int(p[1]), label=f"mb#{i+1}"))

    return VisionPlan(
        instruction=instruction or rtype,
        clicks=clicks,
        drags=drags or None,
        backend="multibot",
        notes=f"Multibot {rtype} answers={str(answers)[:120]}",
    )


def try_solve_with_multibot(page) -> bool | None:
    """
    In-browser Multibot solve for Riot canvas challenges.
    Returns True/False/None like yescaptcha.try_solve_task_grid.
    """
    from yescaptcha_click import (
        extract_anchor_queries_from_frame,
        extract_prompt_from_frame,
        export_challenge_canvas,
        frame_has_challenge_canvas,
        click_verify_in_frame,
        checkbox_is_checked,
    )
    from vision_captcha import find_hcaptcha_frame, ensure_challenge_iframe_on_screen
    from human_mouse import drag_human, click_human, human_mouse_enabled

    if not (os.getenv("MULTIBOT_API_KEY") or os.getenv("MULTIBOT_KEY") or "").strip():
        return None

    ensure_challenge_iframe_on_screen(page)
    frame = find_hcaptcha_frame(page)
    if frame is None or not frame_has_challenge_canvas(frame):
        return None

    question = extract_prompt_from_frame(frame) or ""
    canvas_info = export_challenge_canvas(frame)
    if not canvas_info:
        return None
    examples = extract_anchor_queries_from_frame(frame)
    rtype = "Drag" if "drag" in question.lower() else "Canvas"
    try:
        answers = solve_canvas_or_drag(
            image_b64=canvas_info["b64"],
            question=question,
            request_type=rtype,
            examples=examples or None,
        )
    except Exception as exc:
        _log(f"solve failed: {exc}")
        return False

    # Reuse YesCaptcha canvas click applicator via a synthetic box when possible
    from yescaptcha_click import _click_canvas_box

    box: Any = answers
    if isinstance(answers, dict) and "box" in answers:
        box = answers["box"]
    upload_size = (
        int(canvas_info.get("width") or 0),
        int(canvas_info.get("height") or 0),
    )
    applied = _click_canvas_box(
        page, frame, canvas_info, box, upload_size=upload_size
    )
    if applied <= 0 and human_mouse_enabled():
        # Best-effort: if answers are point pairs in display space
        _log(f"canvas applicator applied 0; raw answers={str(answers)[:160]}")
    if applied <= 0:
        return False
    page.wait_for_timeout(400)
    click_verify_in_frame(frame)
    page.wait_for_timeout(2500)
    if checkbox_is_checked(page):
        _log("checkbox checked — solved")
        return True
    _log("round incomplete")
    return False
