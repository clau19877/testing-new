"""
YesCaptcha HCaptchaClassification for in-browser hCaptcha.

Primary path (official Selenium DEMO):
  https://yescaptcha.atlassian.net/wiki/spaces/YESCAPTCHA/pages/30113813
  1) read .prompt-text question
  2) download each .task-image tile → resize 100x100 → base64
  3) createTask HCaptchaClassification with queries=[tile0..tile8]
  4) click .task-image indices where solution.objects is true
  5) click .button-submit / Verify; retry if checkbox not checked

Fallback path (new styles: drag / point / number-match screenshots):
  send one full challenge screenshot (+ anchors) and apply box coords.
"""

from __future__ import annotations

import base64
import os
import random
import re
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

_URL_IN_STYLE = re.compile(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', re.I)


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


def _pil_to_jpeg_b64(img, *, quality: int = 90) -> str:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def resize_bytes_to_b64(data: bytes, size: tuple[int, int] = (100, 100)) -> str:
    """DEMO helper: resize tile to 100x100 JPEG base64 (no data: prefix)."""
    from PIL import Image

    img = Image.open(BytesIO(data)).convert("RGB")
    img = img.resize(size, Image.Resampling.LANCZOS)
    return _pil_to_jpeg_b64(img, quality=90)


def _image_to_jpeg_b64(path: Path, *, max_side: int = 900) -> tuple[str, tuple[int, int]]:
    from PIL import Image

    img = Image.open(path).convert("RGB")
    orig = img.size
    w, h = orig
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    return _pil_to_jpeg_b64(img, quality=85), orig


def extract_anchors(screenshot: Path) -> list[str]:
    """Crop requirement/icon strip when DOM anchors are unavailable."""
    from PIL import Image

    img = Image.open(screenshot).convert("RGB")
    w, h = img.size
    y0, y1 = int(h * 0.12), int(h * 0.36)
    if y1 <= y0 + 20:
        return []
    strip = img.crop((0, y0, w, y1))
    anchors: list[str] = [_pil_to_jpeg_b64(strip)]
    sw, sh = strip.size
    for cx in (0.28, 0.55, 0.82):
        x = int(sw * cx) - 36
        crop = strip.crop((max(0, x), 6, min(sw, x + 72), sh - 4))
        if crop.size[0] >= 24 and crop.size[1] >= 24:
            anchors.append(_pil_to_jpeg_b64(crop))
    return anchors


def extract_question(screenshot: Path) -> str:
    try:
        from twocaptcha_click import extract_instruction

        return extract_instruction(screenshot)
    except Exception:
        return "Solve this hCaptcha challenge"


def classify_queries(
    queries: list[str],
    question: str,
    *,
    anchors: list[str] | None = None,
) -> dict[str, Any]:
    """
    Call HCaptchaClassification (DEMO createTask shape).

    queries: list of base64 tile JPEGs (classic 3x3) OR one full screenshot.
    Returns solution dict (objects / box / clicks / type).
    """
    if not queries:
        raise CaptchaSolverError("YesCaptcha queries empty")
    key = _api_key()
    task: dict[str, Any] = {
        "type": "HCaptchaClassification",
        "queries": queries,
        "question": (question or "").strip() or "Please solve this captcha",
        "anchors": anchors or [],
    }
    _log(
        f"createTask question={task['question'][:90]!r} "
        f"queries={len(queries)} anchors={len(task['anchors'])}"
    )
    resp = requests.post(
        YESCAPTCHA_CREATE,
        json={"clientKey": key, "task": task},
        timeout=60,
    )
    data = resp.json()
    if data.get("errorId"):
        raise CaptchaSolverError(f"YesCaptcha createTask: {data}")
    if (data.get("status") or "").lower() == "ready" and isinstance(
        data.get("solution"), dict
    ):
        return data["solution"]
    task_id = data.get("taskId")
    if not task_id:
        raise CaptchaSolverError(f"YesCaptcha missing taskId: {data}")
    return poll_result(str(task_id))


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


def objects_to_indices(objects: Any) -> list[int]:
    """
    DEMO: solution.objects is [true,false,…] matching query order.
    Also accept index lists / int lists.
    """
    if not isinstance(objects, list) or not objects:
        return []
    if all(isinstance(x, bool) for x in objects):
        return [i for i, x in enumerate(objects) if x]
    # Some APIs return indices directly
    if all(isinstance(x, int) and not isinstance(x, bool) for x in objects):
        return [int(x) for x in objects if 0 <= int(x) < 16]
    # Mixed / stringy
    out: list[int] = []
    for i, x in enumerate(objects):
        if x is True or x == 1 or x == "1" or x == "true":
            out.append(i)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            xi = int(x)
            if 0 <= xi < 16:
                out.append(xi)
    return sorted(set(out))


# ---------------------------------------------------------------------------
# Playwright: classic 3x3 task-grid path (Selenium DEMO port)
# ---------------------------------------------------------------------------

def frame_has_task_grid(frame) -> bool:
    if frame is None:
        return False
    try:
        n = frame.locator(".task-image").count()
        return n >= 9
    except Exception:
        return False


def frame_has_challenge_canvas(frame) -> bool:
    """Riot Enterprise / new-style hCaptcha draws the puzzle on <canvas>."""
    if frame is None:
        return False
    try:
        return frame.locator("canvas").count() >= 1
    except Exception:
        return False


def extract_prompt_from_frame(frame) -> str:
    for sel in (".prompt-text", ".prompt-padding", "#prompt-question", "[class*='prompt']"):
        try:
            loc = frame.locator(sel).first
            if loc.count():
                txt = (loc.inner_text(timeout=800) or "").strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


def export_challenge_canvas(frame) -> dict[str, Any] | None:
    """
    Export the challenge <canvas> as JPEG base64 + geometry.

    YesCaptcha new-style docs: prefer canvas→jpg; scale clicks by
    display_width / upload_width.
    """
    try:
        data = frame.evaluate(
            """() => {
              const c = document.querySelector('canvas');
              if (!c) return null;
              const r = c.getBoundingClientRect();
              let b64 = '';
              try { b64 = c.toDataURL('image/jpeg', 0.85); } catch (e) { return {err: String(e)}; }
              return {
                dataUrl: b64,
                width: c.width || 0,
                height: c.height || 0,
                displayWidth: r.width || 0,
                displayHeight: r.height || 0,
                left: r.left || 0,
                top: r.top || 0,
              };
            }"""
        )
    except Exception as exc:
        _log(f"canvas export failed: {exc}")
        return None
    if not data or not data.get("dataUrl"):
        _log(f"canvas export empty: {data}")
        return None
    raw = data["dataUrl"]
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    data["b64"] = raw
    return data


def _download_url_to_b64(url: str, *, size: tuple[int, int] = (100, 100)) -> str | None:
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return resize_bytes_to_b64(r.content, size=size)
    except Exception as exc:
        _log(f"tile download failed: {exc}")
        return None


def extract_tile_queries_from_frame(frame) -> list[str]:
    """
    DEMO: each .task-image .image has background:url("https://…").
    Download + resize to 100x100.
    """
    styles = frame.evaluate(
        """() => {
          const nodes = [...document.querySelectorAll(
            '.task-image .image-wrapper .image, .task-image .image, .task .image'
          )];
          return nodes.map((el) => el.getAttribute('style') || '');
        }"""
    )
    queries: list[str] = []
    for i, style in enumerate(styles or []):
        m = _URL_IN_STYLE.search(style or "")
        if not m:
            _log(f"tile#{i} missing background url")
            continue
        b64 = _download_url_to_b64(m.group(1))
        if b64:
            queries.append(b64)
    # Fallback: screenshot each .task-image if URL scrape failed
    if len(queries) < 9:
        queries = []
        tiles = frame.locator(".task-image")
        n = min(tiles.count(), 12)
        for i in range(n):
            try:
                png = tiles.nth(i).screenshot(type="png")
                queries.append(resize_bytes_to_b64(png, size=(100, 100)))
            except Exception as exc:
                _log(f"tile screenshot #{i} failed: {exc}")
    return queries


def extract_anchor_queries_from_frame(frame) -> list[str]:
    """Pull small example/reference images from the prompt area when present."""
    payload = frame.evaluate(
        """() => {
          const sels = [
            '.challenge-example .image',
            '.challenge-example',
            '.examples .image',
            '.example-wrapper .image',
            '.prompt .image',
            '[class*="example"] .image',
            '.crumbs-wrapper .image',
            '.challenge-header .image',
          ];
          const urls = [];
          const shots = [];
          for (const sel of sels) {
            for (const el of document.querySelectorAll(sel)) {
              const st = el.getAttribute('style') || '';
              const m = /url\\(["']?(https?:\\/\\/[^"')\\s]+)["']?\\)/i.exec(st);
              if (m) urls.push(m[1]);
              if (el.tagName === 'IMG' && el.src && !el.src.startsWith('data:')) urls.push(el.src);
            }
          }
          // Screenshot each visible example .image if no URL (canvas-era widgets)
          for (const el of document.querySelectorAll('.challenge-example .image, .examples .image, .example-wrapper .image')) {
            const r = el.getBoundingClientRect();
            if (r.width >= 20 && r.height >= 20) {
              shots.push({x:r.x, y:r.y, w:r.width, h:r.height});
            }
          }
          return {urls: [...new Set(urls)], shots};
        }"""
    )
    anchors: list[str] = []
    for url in (payload or {}).get("urls") or []:
        b64 = _download_url_to_b64(url, size=(100, 100))
        if b64:
            anchors.append(b64)
    if anchors:
        return anchors
    # Fallback: element screenshots via Playwright locators.
    # Riot often paints examples into the header; grab the whole strip too.
    for sel in (
        ".challenge-example .image",
        ".examples .image",
        ".example-wrapper .image",
        ".challenge-example",
        ".examples",
        ".example-wrapper",
        ".challenge-prompt",
    ):
        try:
            locs = frame.locator(sel)
            n = min(locs.count(), 6)
            for i in range(n):
                try:
                    el = locs.nth(i)
                    if not el.is_visible():
                        continue
                    box = el.bounding_box(timeout=800)
                    if not box or box["width"] < 30 or box["height"] < 20:
                        continue
                    png = el.screenshot(type="png")
                    # Keep prompt-strip larger; individual icons at 100x100
                    if box["width"] >= 160:
                        from PIL import Image

                        img = Image.open(BytesIO(png)).convert("RGB")
                        anchors.append(_pil_to_jpeg_b64(img, quality=90))
                    else:
                        anchors.append(resize_bytes_to_b64(png, size=(100, 100)))
                except Exception:
                    continue
            if anchors:
                break
        except Exception:
            continue
    return anchors


def checkbox_is_checked(page) -> bool:
    """DEMO get_is_successful: checkbox aria-checked=true."""
    try:
        for frame in page.frames:
            url = (frame.url or "").lower()
            if "hcaptcha" not in url:
                continue
            if "checkbox" not in url and "frame=checkbox" not in url:
                continue
            loc = frame.locator("#checkbox, #anchor #checkbox, [role='checkbox']").first
            if not loc.count():
                continue
            checked = (loc.get_attribute("aria-checked") or "").lower()
            if checked == "true":
                return True
    except Exception:
        pass
    return False


def click_task_indices(frame, indices: list[int]) -> int:
    """DEMO: click .task-image nodes at recognized indices."""
    tiles = frame.locator(".task-image")
    n = tiles.count()
    applied = 0
    for idx in indices:
        if idx < 0 or idx >= n:
            _log(f"skip index {idx} (n={n})")
            continue
        try:
            tiles.nth(idx).click(timeout=2500)
            applied += 1
            _log(f"clicked task-image[{idx}]")
            time.sleep(0.25 + random.random() * 0.45)
        except Exception as exc:
            _log(f"click task-image[{idx}] failed: {exc}")
    return applied


def click_verify_in_frame(frame) -> bool:
    for sel in (
        'div.button-submit:has-text("Verify")',
        'div.button-submit:has-text("Next")',
        ".button-submit",
    ):
        try:
            loc = frame.locator(sel).first
            if not loc.count() or not loc.is_visible():
                continue
            txt = (loc.inner_text(timeout=400) or "").strip().lower()
            if "skip" in txt:
                continue
            loc.click(timeout=2000)
            _log(f"clicked verify via {sel!r} text={txt!r}")
            return True
        except Exception:
            continue
    return False


def _click_canvas_box(
    page,
    frame,
    canvas_info: dict[str, Any],
    box: Any,
    *,
    upload_size: tuple[int, int],
) -> int:
    """
    Apply YesCaptcha box / clicks onto the challenge canvas.

    Scale: display = upload_coord * displaySize / uploadSize
    (per YesCaptcha new-style docs). Then offset by canvas getBoundingClientRect
    in the *frame*, converted to page coords via iframe bbox.
    """
    from vision_captcha import find_challenge_iframe_locator

    clicks = _parse_box_clicks(box, upload_size=upload_size, orig_size=upload_size)
    # Prefer structured clicks list
    if not clicks and isinstance(box, list) is False:
        pass
    drags = _parse_box_drags(box, upload_size=upload_size, orig_size=upload_size)

    disp_w = float(canvas_info.get("displayWidth") or 0) or float(
        canvas_info.get("width") or 1
    )
    disp_h = float(canvas_info.get("displayHeight") or 0) or float(
        canvas_info.get("height") or 1
    )
    up_w, up_h = upload_size
    # If we uploaded the raw canvas bitmap, upload_size == canvas width/height.
    # Clicks are in that space; scale to display CSS pixels inside the frame.
    sx = disp_w / max(up_w, 1)
    sy = disp_h / max(up_h, 1)

    iframe = find_challenge_iframe_locator(page)
    iframe_box = None
    if iframe is not None:
        try:
            iframe_box = iframe.bounding_box(timeout=2000)
        except Exception:
            iframe_box = None

    try:
        from human_mouse import click_human, drag_human, human_mouse_enabled

        use_human = human_mouse_enabled()
    except Exception:
        use_human = False

    applied = 0
    for i, c in enumerate(clicks):
        # c is in upload/canvas bitmap space
        local_x = c["x"] * sx
        local_y = c["y"] * sy
        # canvas rect inside frame viewport
        cx = float(canvas_info.get("left") or 0) + local_x
        cy = float(canvas_info.get("top") or 0) + local_y
        if iframe_box:
            px = iframe_box["x"] + cx
            py = iframe_box["y"] + cy
        else:
            px, py = cx, cy
        try:
            if use_human:
                click_human(page, float(px), float(py))
                _log(
                    f"canvas human-click #{i+1} bitmap=({c['x']},{c['y']}) "
                    f"page=({px:.0f},{py:.0f})"
                )
            else:
                # Prefer clicking the canvas element at CSS offset
                frame.locator("canvas").first.click(
                    position={"x": float(local_x), "y": float(local_y)},
                    timeout=2500,
                    force=True,
                )
                _log(
                    f"canvas click #{i+1} bitmap=({c['x']},{c['y']}) "
                    f"disp=({local_x:.0f},{local_y:.0f})"
                )
            applied += 1
            time.sleep(0.3 + random.random() * 0.35)
        except Exception as exc:
            _log(f"canvas element click failed ({exc}); page mouse @ ({px:.0f},{py:.0f})")
            try:
                page.mouse.click(px, py)
                applied += 1
                time.sleep(0.3)
            except Exception as exc2:
                _log(f"page mouse click failed: {exc2}")

    for i, d in enumerate(drags):
        x1, y1 = d["x1"] * sx, d["y1"] * sy
        x2, y2 = d["x2"] * sx, d["y2"] * sy
        if iframe_box:
            p1 = (iframe_box["x"] + float(canvas_info.get("left") or 0) + x1,
                  iframe_box["y"] + float(canvas_info.get("top") or 0) + y1)
            p2 = (iframe_box["x"] + float(canvas_info.get("left") or 0) + x2,
                  iframe_box["y"] + float(canvas_info.get("top") or 0) + y2)
        else:
            p1 = (float(canvas_info.get("left") or 0) + x1,
                  float(canvas_info.get("top") or 0) + y1)
            p2 = (float(canvas_info.get("left") or 0) + x2,
                  float(canvas_info.get("top") or 0) + y2)
        _log(f"canvas drag #{i+1} ({x1:.0f},{y1:.0f})->({x2:.0f},{y2:.0f})")
        if use_human:
            drag_human(page, float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]))
        else:
            page.mouse.move(*p1)
            page.wait_for_timeout(150)
            page.mouse.down()
            page.wait_for_timeout(200)
            steps = 24
            for s in range(1, steps + 1):
                page.mouse.move(
                    p1[0] + (p2[0] - p1[0]) * s / steps,
                    p1[1] + (p2[1] - p1[1]) * s / steps,
                )
                page.wait_for_timeout(20)
            page.mouse.up()
        applied += 1
        page.wait_for_timeout(600)
    return applied


def try_solve_task_grid(page) -> bool | None:
    """
    YesCaptcha in-browser solve.

    1) Classic DEMO path: 9× .task-image → objects[] → click indices
    2) Riot / new-style: export <canvas> (+ .examples anchors) → box coords

    Returns:
      True  — checkbox checked / captcha cleared
      False — ran a round but not cleared yet (caller may retry)
      None  — neither task-grid nor canvas available
    """
    from PIL import Image

    from vision_captcha import find_hcaptcha_frame, ensure_challenge_iframe_on_screen

    ensure_challenge_iframe_on_screen(page)
    frame = find_hcaptcha_frame(page)
    if frame is None:
        return None

    # --- Classic 3x3 DEMO path ---
    if frame_has_task_grid(frame):
        question = extract_prompt_from_frame(frame)
        queries = extract_tile_queries_from_frame(frame)
        anchors = extract_anchor_queries_from_frame(frame)
        _log(
            f"task-grid question={question[:80]!r} tiles={len(queries)} "
            f"dom_anchors={len(anchors)}"
        )
        if len(queries) < 9:
            _log(f"expected 9 tiles, got {len(queries)} — abort tile path")
            return None

        sol = classify_queries(queries, question, anchors=anchors)
        objects = sol.get("objects")
        indices = objects_to_indices(objects)
        if not indices and isinstance(sol.get("top_k"), list):
            indices = objects_to_indices(sol["top_k"])
        _log(f"objects={objects} indices={indices} labels={str(sol.get('labels'))[:120]}")

        if not indices:
            try:
                skip = frame.locator('div.button-submit:has-text("Skip")').first
                if skip.count() and skip.is_visible():
                    skip.click(timeout=1500)
                    _log("no matches — clicked Skip")
                    page.wait_for_timeout(1500)
                    return False
            except Exception:
                pass
            _log("no positive tile indices")
            return False

        applied = click_task_indices(frame, indices)
        if applied <= 0:
            return False
        page.wait_for_timeout(400)
        click_verify_in_frame(frame)
        page.wait_for_timeout(2500)
        if checkbox_is_checked(page):
            _log("checkbox aria-checked=true — solved")
            return True
        _log("task grid round incomplete")
        return False

    # --- New-style / Riot Enterprise: canvas + optional examples ---
    if not frame_has_challenge_canvas(frame):
        return None

    # Wait briefly for canvas paint / examples
    page.wait_for_timeout(600)
    question = extract_prompt_from_frame(frame)
    canvas_info = export_challenge_canvas(frame)
    if not canvas_info:
        return None
    anchors = extract_anchor_queries_from_frame(frame)
    b64 = canvas_info["b64"]
    # Decode to know upload pixel size (may match canvas.width/height)
    try:
        up = Image.open(BytesIO(base64.b64decode(b64)))
        upload_size = up.size
    except Exception:
        upload_size = (
            int(canvas_info.get("width") or 0),
            int(canvas_info.get("height") or 0),
        )
    _log(
        f"canvas-mode question={question[:80]!r} "
        f"bitmap={canvas_info.get('width')}x{canvas_info.get('height')} "
        f"display={canvas_info.get('displayWidth'):.0f}x{canvas_info.get('displayHeight'):.0f} "
        f"upload={upload_size} anchors={len(anchors)}"
    )

    sol = classify_queries([b64], question, anchors=anchors)
    sol_type = (sol.get("type") or "").lower()
    box = sol.get("box")
    if not box and isinstance(sol.get("clicks"), list):
        flat: list[Any] = []
        for item in sol["clicks"]:
            if isinstance(item, dict) and "x" in item and "y" in item:
                flat.extend([item["x"], item["y"]])
        box = flat
    _log(f"canvas solution type={sol_type or 'click'} box={str(box)[:140]}")

    # Rare: classifier returns objects for a reconstructed grid — ignore here
    if not box:
        _log("canvas-mode empty box")
        return False

    applied = _click_canvas_box(
        page, frame, canvas_info, box, upload_size=upload_size
    )
    if applied <= 0:
        return False
    page.wait_for_timeout(400)
    click_verify_in_frame(frame)
    page.wait_for_timeout(2500)

    if checkbox_is_checked(page):
        _log("checkbox aria-checked=true — solved")
        return True
    _log("canvas-mode round incomplete")
    return False


# ---------------------------------------------------------------------------
# Screenshot / coordinate fallback (new styles)
# ---------------------------------------------------------------------------

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
    clicks: list[dict[str, int]] = []
    if not box:
        return clicks
    if isinstance(box, list) and box and isinstance(box[0], dict):
        return clicks
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
    """Screenshot-mode Classification (drag / point / new styles)."""
    from PIL import Image

    from vision_captcha import ClickTarget, DragTarget, VisionPlan

    question = extract_question(screenshot)
    if extra_comment:
        question = f"{question}. {extra_comment.strip()}"

    b64, _ = _image_to_jpeg_b64(screenshot)
    up = Image.open(BytesIO(base64.b64decode(b64)))
    upload_size = up.size
    orig_size = Image.open(screenshot).size
    anchors = extract_anchors(screenshot)
    _log(f"screenshot-mode anchors={len(anchors)}")

    sol = classify_queries([b64], question, anchors=anchors)
    sol_type = (sol.get("type") or "").lower()
    box = sol.get("box")
    if not box and isinstance(sol.get("clicks"), list):
        flat: list[Any] = []
        for item in sol["clicks"]:
            if isinstance(item, dict) and "x" in item and "y" in item:
                flat.extend([item["x"], item["y"]])
        box = flat
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

    # Classic objects[] from a mistakenly-sent multi-tile crop is rare here
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
        notes=f"YesCaptcha Classification ({len(clicks)} clicks, anchors={len(anchors)})",
    )
