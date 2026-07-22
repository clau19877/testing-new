#!/usr/bin/env python3
"""
Focused Multibot format/timing diagnostic.

Gets to ONE live Riot hCaptcha challenge, then submits it to Multibot in a
few candidate formats to learn which image + request_type actually resolves,
and what the ready-answer shape looks like. Saves the challenge images so the
result can be inspected.

Usage:
  DISPLAY=:99 PROXY_INDEX=60 .venv/bin/python diag_multibot.py
"""

from __future__ import annotations

import base64
import json
import os
import time
from io import BytesIO
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("NONINTERACTIVE", "1")

from proxyutil import load_proxy_list  # noqa: E402
from stealth_browser import launch_stealth_browser  # noqa: E402

CREATE = "https://api.multibot.cloud/createTask"
RESULT = "https://api.multibot.cloud/getTaskResult"


def log(m: str) -> None:
    print(f"[diag-mb] {m}", flush=True)


def submit_and_poll(key: str, task_type: str, task, *, timeout: float = 180.0):
    r = requests.post(CREATE, json={"clientKey": key, "type": task_type, "task": task}, timeout=30)
    body = r.json()
    if body.get("errorId"):
        return {"create_error": body}
    tid = body.get("taskId")
    log(f"  taskId={tid}")
    t0 = time.time()
    shape_logged = False
    while time.time() - t0 < timeout:
        time.sleep(3)
        rr = requests.post(RESULT, json={"clientKey": key, "taskId": tid}, timeout=30)
        b = rr.json()
        if not shape_logged:
            log(f"  result keys={list(b.keys())} status={b.get('status')!r}")
            shape_logged = True
        st = b.get("status")
        if st in ("ready", "success", "solved", "completed"):
            return {"ok": b}
        if st in ("failed", "error") or b.get("errorId"):
            return {"failed": b}
    return {"timeout": True, "elapsed": round(time.time() - t0, 1)}


def main() -> int:
    key = (os.getenv("MULTIBOT_API_KEY") or "").strip()
    user = os.getenv("RIOT_USERNAME") or ""
    password = os.getenv("RIOT_PASSWORD") or ""
    proxies = load_proxy_list(os.getenv("PROXY_LIST") or "data/proxies.txt")
    idx = int(os.getenv("PROXY_INDEX") or "0")
    proxy = proxies[idx % len(proxies)]

    out: dict = {}
    with launch_stealth_browser(headed=True, proxy=proxy) as (_p, _b, context):
        page = context.new_page()
        page.set_default_timeout(90_000)
        page.goto("https://account.riotgames.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        for sel in ('button[aria-label="Close this dialog"]', 'button.osano-cm-dialog__close'):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=1500); page.wait_for_timeout(300)
            except Exception:
                pass
        page.locator('input[name="username"]').first.fill(user)
        page.locator('input[name="password"]').first.fill(password)
        for sel in ('button[data-testid="btn-signin-submit"]', 'button[type="submit"]'):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=3000); break
            except Exception:
                pass
        # wait for challenge
        from vision_captcha import (
            captcha_visible, find_hcaptcha_frame, ensure_challenge_iframe_on_screen,
            screenshot_challenge, challenge_content_ready, wait_for_challenge_canvas,
        )
        from yescaptcha_click import (
            extract_prompt_from_frame, export_challenge_canvas,
            frame_has_challenge_canvas,
        )
        for _ in range(20):
            if captcha_visible(page):
                break
            page.wait_for_timeout(500)
        ensure_challenge_iframe_on_screen(page)

        # Wait for the challenge to actually render (prompt + painted canvas).
        question = ""
        frame = None
        for i in range(30):
            page.wait_for_timeout(1000)
            frame = find_hcaptcha_frame(page)
            if frame is None:
                continue
            question = (extract_prompt_from_frame(frame) or "").strip()
            has_canvas = frame_has_challenge_canvas(frame)
            probe_shot = screenshot_challenge(page, tag=f"diag_mb_probe{i}")
            ready = challenge_content_ready(probe_shot)
            if question and (has_canvas or ready):
                log(f"challenge ready after {i+1}s: q={question!r} canvas={has_canvas} ready={ready}")
                break
        wait_for_challenge_canvas(page, timeout_s=8.0)
        page.wait_for_timeout(800)
        if frame is None:
            log("no challenge frame")
            return 2
        out["question"] = question
        log(f"final question={question!r} has_canvas={frame_has_challenge_canvas(frame)}")

        # Image A: clean canvas bitmap via toDataURL (retry until painted)
        canvas_b64 = None
        for _ in range(6):
            canvas_info = export_challenge_canvas(frame)
            canvas_b64 = canvas_info.get("b64") if canvas_info else None
            if canvas_b64:
                (ROOT / "debug" / "diag_mb_canvas.jpg").write_bytes(base64.b64decode(canvas_b64))
                log(f"canvas bitmap {canvas_info.get('width')}x{canvas_info.get('height')}")
                break
            page.wait_for_timeout(1000)

        # Image B: clipped challenge screenshot
        shot = screenshot_challenge(page, tag="diag_mb")
        (ROOT / "debug" / "diag_mb_clip.png").write_bytes(Path(shot).read_bytes())
        img_bytes = Path(shot).read_bytes()
        from PIL import Image
        im = Image.open(BytesIO(img_bytes)).convert("RGB")
        buf = BytesIO(); im.save(buf, format="JPEG", quality=90)
        shot_b64 = base64.b64encode(buf.getvalue()).decode()

        rtype = "Drag" if "drag" in question.lower() else "Canvas"

        trials = []
        if canvas_b64:
            trials.append(("canvas_bitmap", {"request_type": rtype, "question": question, "body": canvas_b64}))
        trials.append(("clip_screenshot", {"request_type": rtype, "question": question, "body": shot_b64}))
        # Also try Grid type on the clip in case it's a tiled grid
        trials.append(("clip_grid", {"request_type": "Grid", "question": question, "body": shot_b64}))

        for name, task in trials:
            log(f"--- trial {name} ({task['request_type']}) ---")
            res = submit_and_poll(key, "hCaptchaBase64", task, timeout=float(os.getenv("MB_TIMEOUT") or "150"))
            out[name] = res
            log(f"  => {json.dumps(res)[:400]}")

    (ROOT / "debug" / "diag_multibot_report.json").write_text(json.dumps(out, indent=2))
    log(f"wrote debug/diag_multibot_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
