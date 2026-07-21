"""
In-browser vision captcha solver.

Backends:
  - ocr:     local Tesseract (good for Riot letter-grid challenges)
  - openai:  GPT-4o vision (needs OPENAI_API_KEY)
  - agent:   write screenshot + wait for debug/vision_response.json
             (Cursor agent can fill clicks after reading the image)
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

DEBUG_DIR = Path(__file__).resolve().parent / "debug"


@dataclass
class ClickTarget:
    x: int  # pixel in screenshot coords
    y: int
    label: str = ""
    times: int = 1


@dataclass
class DragTarget:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str = ""


@dataclass
class VisionPlan:
    instruction: str
    clicks: list[ClickTarget]
    backend: str
    notes: str = ""
    drags: list[DragTarget] | None = None


def ensure_debug() -> Path:
    DEBUG_DIR.mkdir(exist_ok=True)
    return DEBUG_DIR


def _log(msg: str) -> None:
    print(f"[vision] {msg}", flush=True)


# ---------------------------------------------------------------------------
# OCR letter-grid solver (Riot-style: "Click each letter N times")
# ---------------------------------------------------------------------------

def _parse_letter_requirements(text: str) -> dict[str, int]:
    """
    Parse prompts like:
      Click each letter the exact number of times listed
      W 1   F 1
    or 'W: 1 time', 'click W once', etc.
    """
    req: dict[str, int] = {}
    upper = text.upper()
    # Letter followed by count nearby
    for m in re.finditer(r"\b([A-Z])\b[^\nA-Z]{0,20}?\b(\d+)\b", upper):
        req[m.group(1)] = int(m.group(2))
    for m in re.finditer(r"\b(\d+)\b[^\nA-Z]{0,12}?\b([A-Z])\b", upper):
        req[m.group(2)] = int(m.group(1))
    # "ONCE" / "TWICE"
    for m in re.finditer(r"\b([A-Z])\b[^\nA-Z]{0,20}?\bONCE\b", upper):
        req[m.group(1)] = 1
    for m in re.finditer(r"\b([A-Z])\b[^\nA-Z]{0,20}?\bTWICE\b", upper):
        req[m.group(1)] = 2
    return req


def _ocr_image(path: Path) -> tuple[str, list[dict[str, Any]]]:
    import pytesseract

    img = Image.open(path)
    text = pytesseract.image_to_string(img)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    boxes: list[dict[str, Any]] = []
    n = len(data["text"])
    for i in range(n):
        raw = (data["text"][i] or "").strip()
        conf = float(data["conf"][i]) if str(data["conf"][i]).lstrip("-").isdigit() else -1
        if not raw or conf < 40:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        boxes.append(
            {
                "text": raw,
                "conf": conf,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "cx": x + w // 2,
                "cy": y + h // 2,
            }
        )
    return text, boxes


def _find_letter_cells(boxes: list[dict[str, Any]], img_h: int) -> list[dict[str, Any]]:
    """Prefer single-letter boxes in the lower/grid portion of the challenge."""
    letters = []
    for b in boxes:
        t = re.sub(r"[^A-Za-z]", "", b["text"])
        if len(t) == 1 and t.isalpha():
            letters.append({**b, "letter": t.upper()})
    if not letters:
        return []
    # Grid letters are usually larger and in the bottom ~70%
    med_h = float(np.median([b["h"] for b in letters]))
    grid = [b for b in letters if b["h"] >= med_h * 0.7 and b["cy"] > img_h * 0.25]
    return grid or letters


def _ocr_single_letter(crop_bgr: np.ndarray) -> str:
    """Vote across preprocess variants for a single capital letter tile."""
    import pytesseract

    crop = cv2.resize(crop_bgr, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Prefer dark letter on light background for Tesseract
    candidates = [thr, 255 - thr, gray]
    cfg = "--psm 10 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    votes: dict[str, float] = {}
    for im in candidates:
        # If mostly dark, invert so letter is dark on white
        work = im
        if isinstance(work, np.ndarray) and work.ndim == 2 and (work < 128).mean() > 0.55:
            work = 255 - work
        raw = pytesseract.image_to_string(work, config=cfg).strip().upper()
        letters = "".join(ch for ch in raw if ch.isalpha())
        if len(letters) == 1:
            votes[letters] = votes.get(letters, 0.0) + 1.0
        elif len(letters) > 1:
            votes[letters[0]] = votes.get(letters[0], 0.0) + 0.25
    if not votes:
        return ""
    return max(votes, key=votes.get)


def _detect_teal_tiles(img_bgr: np.ndarray) -> list[dict[str, Any]]:
    """
    Segment Riot/hCaptcha teal letter tiles (white/teal squares on busy bg).
    Returns dicts with letter, cx, cy, x, y, w, h.
    """
    h, w = img_bgr.shape[:2]
    b, g, r = cv2.split(img_bgr)
    teal = (
        (g.astype(int) > 100)
        & (b.astype(int) > 80)
        & (r.astype(int) < 140)
        & (g.astype(int) > r.astype(int) + 15)
    ).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    teal = cv2.morphologyEx(teal, cv2.MORPH_CLOSE, kernel, iterations=2)
    teal = cv2.morphologyEx(teal, cv2.MORPH_OPEN, kernel, iterations=1)
    contours, _ = cv2.findContours(teal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tiles: list[dict[str, Any]] = []
    for c in contours:
        x, y, ww, hh = cv2.boundingRect(c)
        area = ww * hh
        if area < 900 or area > 9000:
            continue
        ar = ww / max(hh, 1)
        if not (0.7 < ar < 1.4):
            continue
        # Skip footer chrome / tiny icons
        if y > h * 0.88:
            continue
        crop = img_bgr[y : y + hh, x : x + ww]
        letter = _ocr_single_letter(crop)
        tiles.append(
            {
                "letter": letter,
                "x": x,
                "y": y,
                "w": ww,
                "h": hh,
                "cx": x + ww // 2,
                "cy": y + hh // 2,
                "area": area,
            }
        )
    return tiles


def _parse_req_times_near(img_bgr: np.ndarray, tile: dict[str, Any]) -> int:
    """Look left of a requirement tile for '1x' / '2x' style counts."""
    import pytesseract

    h, w = img_bgr.shape[:2]
    x0 = max(0, tile["x"] - 90)
    y0 = max(0, tile["y"] - 10)
    x1 = tile["x"] + 5
    y1 = min(h, tile["y"] + tile["h"] + 10)
    crop = img_bgr[y0:y1, x0:x1]
    if crop.size == 0:
        return 1
    crop2 = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    raw = pytesseract.image_to_string(crop2, config="--psm 7").upper()
    m = re.search(r"(\d)\s*X", raw)
    if m:
        return max(1, int(m.group(1)))
    m = re.search(r"\b(\d+)\b", raw)
    if m:
        return max(1, int(m.group(1)))
    return 1


def plan_letter_grid_cv(screenshot: Path) -> VisionPlan | None:
    """
    CV path for teal letter-grid challenges.
    Requirement letters sit in a strip above the main 3x4 (or similar) grid.
    """
    img = cv2.imread(str(screenshot))
    if img is None:
        return None
    h, w = img.shape[:2]
    tiles = _detect_teal_tiles(img)
    if len(tiles) < 4:
        _log(f"CV tiles too few: {len(tiles)}")
        return None

    # Cluster by vertical band: requirement strip is above the densest grid band
    ys = sorted(t["cy"] for t in tiles)
    # Main grid usually starts around mid image; req tiles are smaller & higher
    med_area = float(np.median([t["area"] for t in tiles]))
    # Heuristic split: first large vertical gap after top tiles, or y < 0.38h & smaller
    grid_candidates = [t for t in tiles if t["cy"] > h * 0.38 and t["area"] >= med_area * 0.85]
    if len(grid_candidates) < 6:
        grid_candidates = [t for t in tiles if t["cy"] > h * 0.35]
    if not grid_candidates:
        return None
    grid_top = min(t["y"] for t in grid_candidates) - 8
    req_tiles = [
        t
        for t in tiles
        if t["y"] + t["h"] < grid_top and t["cy"] > h * 0.12 and t["letter"]
    ]
    grid_tiles = [t for t in tiles if t["y"] >= grid_top - 5 and t["letter"]]

    _log(
        f"CV req={[ (t['letter'], t['cx'], t['cy']) for t in req_tiles ]} "
        f"grid={[ (t['letter'], t['cx'], t['cy']) for t in grid_tiles ]}"
    )
    if not req_tiles or not grid_tiles:
        return None

    req: dict[str, int] = {}
    for t in sorted(req_tiles, key=lambda z: z["cx"]):
        times = _parse_req_times_near(img, t)
        letter = t["letter"]
        req[letter] = req.get(letter, 0) + times

    clicks: list[ClickTarget] = []
    notes: list[str] = []
    for letter, times in req.items():
        matches = [c for c in grid_tiles if c["letter"] == letter]
        if not matches:
            notes.append(f"letter {letter} not found in grid")
            continue
        # Prefer top-left-most match (stable for duplicates)
        matches.sort(key=lambda c: (c["cy"], c["cx"]))
        cell = matches[0]
        for i in range(times):
            clicks.append(
                ClickTarget(x=cell["cx"], y=cell["cy"], label=f"{letter}#{i+1}", times=1)
            )
    if not clicks:
        return None
    return VisionPlan(
        instruction="Click each letter the exact number of times listed",
        clicks=clicks,
        backend="ocr",
        notes="teal-tile CV; " + ("; ".join(notes) if notes else "ok"),
    )


def plan_ocr(screenshot: Path) -> VisionPlan:
    text, boxes = _ocr_image(screenshot)
    _log(f"OCR text:\n{text}")
    img = Image.open(screenshot)
    w, h = img.size
    lower = text.lower()

    # Drag-style challenges (common on Riot / hCaptcha enterprise)
    if "drag" in lower and ("to the" in lower or " onto " in lower):
        # Heuristic fallback positions when we can't segment icons:
        # astronaut/source mid-left, target mid-bottom — refined by agent/openai when available.
        # Try to locate "Move" handle via OCR boxes for a better source point.
        src = None
        for b in boxes:
            if "move" in b["text"].lower():
                src = (b["cx"], b["cy"] + int(b["h"] * 1.5))
                break
        if not src:
            # purple selection often around mid-left of canvas (below banner ~15% height)
            src = (int(w * 0.35), int(h * 0.55))
        dst = (int(w * 0.50), int(h * 0.72))
        return VisionPlan(
            instruction=text.strip()[:300],
            clicks=[],
            drags=[DragTarget(x1=src[0], y1=src[1], x2=dst[0], y2=dst[1], label="drag")],
            backend="ocr",
            notes="drag heuristic from instruction+Move OCR",
        )

    # Prefer CV teal-tile path for letter grids (stylized fonts break plain OCR)
    cv_plan = plan_letter_grid_cv(screenshot)
    if cv_plan and cv_plan.clicks:
        _log(f"using CV letter-grid plan ({len(cv_plan.clicks)} clicks)")
        return cv_plan

    req = _parse_letter_requirements(text)
    _log(f"letter requirements: {req}")
    cells = _find_letter_cells(boxes, h)
    _log(f"letter cells: {[(c['letter'], c['cx'], c['cy'], c['conf']) for c in cells]}")

    clicks: list[ClickTarget] = []
    notes = []
    if not req:
        notes.append("could not parse letter requirements from prompt")
    for letter, times in req.items():
        matches = [c for c in cells if c["letter"] == letter]
        if not matches:
            notes.append(f"letter {letter} not found in grid")
            continue
        matches.sort(key=lambda c: c["h"] * c["w"], reverse=True)
        cell = matches[0]
        for i in range(times):
            clicks.append(
                ClickTarget(x=cell["cx"], y=cell["cy"], label=f"{letter}#{i+1}", times=1)
            )
    return VisionPlan(
        instruction=text.strip()[:300],
        clicks=clicks,
        backend="ocr",
        notes="; ".join(notes),
    )


# ---------------------------------------------------------------------------
# OpenAI vision backend
# ---------------------------------------------------------------------------

def plan_openai(screenshot: Path) -> VisionPlan:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    from openai import OpenAI
    import base64

    client = OpenAI(api_key=api_key)
    b64 = base64.b64encode(screenshot.read_bytes()).decode()
    img = Image.open(screenshot)
    w, h = img.size
    prompt = f"""This is an hCaptcha challenge screenshot ({w}x{h} px).
Return ONLY JSON:
{{
  "instruction": "short summary",
  "clicks": [{{"x": 123, "y": 456, "label": "tile", "times": 1}}],
  "drags": [{{"x1": 10, "y1": 20, "x2": 200, "y2": 300, "label": "astronaut->ship"}}]
}}
Rules:
- Coordinates are pixels from the TOP-LEFT of THIS image.
- Use clicks for tap/grid challenges; use drags for "drag X to Y" challenges.
- For drag: x1,y1 = source center (object to move), x2,y2 = destination center.
- Prefer empty list over guesses if unsure.
"""
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o"),
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        max_tokens=800,
    )
    raw = resp.choices[0].message.content or ""
    _log(f"OpenAI raw: {raw[:500]}")
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return VisionPlan(instruction=raw[:200], clicks=[], backend="openai", notes="no JSON")
    data = json.loads(m.group(0))
    clicks: list[ClickTarget] = []
    for c in data.get("clicks") or []:
        times = int(c.get("times") or 1)
        for i in range(max(1, times)):
            clicks.append(
                ClickTarget(
                    x=int(c["x"]),
                    y=int(c["y"]),
                    label=str(c.get("label") or f"c{i}"),
                    times=1,
                )
            )
    drags = [
        DragTarget(
            x1=int(d["x1"]),
            y1=int(d["y1"]),
            x2=int(d["x2"]),
            y2=int(d["y2"]),
            label=str(d.get("label") or "drag"),
        )
        for d in (data.get("drags") or [])
    ]
    return VisionPlan(
        instruction=str(data.get("instruction") or ""),
        clicks=clicks,
        drags=drags,
        backend="openai",
    )


# ---------------------------------------------------------------------------
# Agent-file backend (Cursor agent fills response after reading screenshot)
# ---------------------------------------------------------------------------

def plan_agent(screenshot: Path, timeout: float = 180.0) -> VisionPlan:
    """
    Writes debug/vision_request.json and waits for debug/vision_response.json:
      {"clicks":[{"x":..,"y":..,"label":"W","times":1}], "instruction":"..."}
    """
    ensure_debug()
    req_path = DEBUG_DIR / "vision_request.json"
    resp_path = DEBUG_DIR / "vision_response.json"
    if resp_path.exists():
        resp_path.unlink()
    req_path.write_text(
        json.dumps(
            {
                "screenshot": str(screenshot),
                "created": time.time(),
                "hint": "Respond with debug/vision_response.json clicks in screenshot pixel coords",
            },
            indent=2,
        )
    )
    _log(f"waiting for {resp_path} (timeout {timeout:.0f}s)…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if resp_path.exists():
            try:
                data = json.loads(resp_path.read_text())
            except Exception:
                time.sleep(0.5)
                continue
            clicks: list[ClickTarget] = []
            for c in data.get("clicks") or []:
                times = int(c.get("times") or 1)
                for i in range(max(1, times)):
                    clicks.append(
                        ClickTarget(
                            x=int(c["x"]),
                            y=int(c["y"]),
                            label=str(c.get("label") or ""),
                            times=1,
                        )
                    )
            drags = [
                DragTarget(
                    x1=int(d["x1"]),
                    y1=int(d["y1"]),
                    x2=int(d["x2"]),
                    y2=int(d["y2"]),
                    label=str(d.get("label") or "drag"),
                )
                for d in (data.get("drags") or [])
            ]
            return VisionPlan(
                instruction=str(data.get("instruction") or ""),
                clicks=clicks,
                drags=drags,
                backend="agent",
                notes=str(data.get("notes") or ""),
            )
        time.sleep(1.0)
    raise TimeoutError("agent vision_response.json not provided in time")


def plan_for_screenshot(screenshot: Path, backend: str | None = None) -> VisionPlan:
    backend = (backend or os.getenv("VISION_BACKEND") or "auto").lower()
    if backend == "auto":
        if os.getenv("OPENAI_API_KEY"):
            backend = "openai"
        else:
            backend = "ocr"
    _log(f"planning with backend={backend} image={screenshot}")
    if backend == "ocr":
        return plan_ocr(screenshot)
    if backend == "openai":
        return plan_openai(screenshot)
    if backend == "agent":
        return plan_agent(screenshot)
    raise ValueError(f"unknown VISION_BACKEND: {backend}")


# ---------------------------------------------------------------------------
# Playwright integration
# ---------------------------------------------------------------------------

def find_hcaptcha_frame(page):
    """Return the Playwright frame that hosts the hCaptcha challenge, if any."""
    for frame in page.frames:
        url = frame.url or ""
        if "hcaptcha.com" in url and ("frame=" in url or "challenge" in url or "checkbox" in url):
            return frame
    # fallback: any hcaptcha frame
    for frame in page.frames:
        if "hcaptcha" in (frame.url or ""):
            return frame
    return None


def captcha_visible(page) -> bool:
    try:
        if page.locator('iframe[src*="hcaptcha.com"]').count() > 0:
            return True
    except Exception:
        pass
    content = ""
    try:
        content = page.content().lower()
    except Exception:
        pass
    return "hcaptcha" in content or "click each letter" in content


def screenshot_challenge(page, tag: str = "challenge") -> Path:
    """Screenshot the challenge area (prefer challenge iframe bbox)."""
    ensure_debug()
    path = DEBUG_DIR / f"vision_{tag}_{int(time.time())}.png"
    # Try to clip to the challenge iframe
    loc = page.locator('iframe[src*="hcaptcha.com"]').last
    try:
        if loc.count() > 0:
            box = loc.bounding_box(timeout=3000)
            if box and box["width"] > 50:
                page.screenshot(path=str(path), clip=box)
                _log(f"clipped iframe screenshot → {path.name} {box}")
                return path
    except Exception as exc:
        _log(f"iframe clip failed: {exc}")
    page.screenshot(path=str(path), full_page=False)
    _log(f"full viewport screenshot → {path.name}")
    return path


def annotate_plan(screenshot: Path, plan: VisionPlan, tag: str = "annotated") -> Path:
    ensure_debug()
    img = cv2.imread(str(screenshot))
    if img is None:
        return screenshot
    for i, c in enumerate(plan.clicks):
        cv2.circle(img, (c.x, c.y), 12, (0, 0, 255), 2)
        cv2.putText(
            img,
            c.label or str(i + 1),
            (c.x + 8, c.y - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )
    for d in plan.drags or []:
        cv2.circle(img, (d.x1, d.y1), 12, (255, 128, 0), 2)
        cv2.circle(img, (d.x2, d.y2), 12, (0, 255, 0), 2)
        cv2.arrowedLine(img, (d.x1, d.y1), (d.x2, d.y2), (255, 255, 0), 2, tipLength=0.05)
    out = DEBUG_DIR / f"vision_{tag}_{int(time.time())}.png"
    cv2.imwrite(str(out), img)
    _log(f"annotated → {out.name}")
    return out


def apply_clicks(page, plan: VisionPlan, screenshot: Path) -> int:
    """
    Map screenshot-local coords to page coords using the challenge iframe box,
    then click / drag with mouse.
    """
    actions = len(plan.clicks) + len(plan.drags or [])
    if actions == 0:
        _log("no clicks/drags in plan")
        return 0

    offset_x, offset_y = 0.0, 0.0
    loc = page.locator('iframe[src*="hcaptcha.com"]').last
    try:
        if loc.count() > 0:
            box = loc.bounding_box(timeout=3000)
            if box:
                offset_x, offset_y = box["x"], box["y"]
                _log(f"iframe offset ({offset_x:.0f},{offset_y:.0f})")
    except Exception as exc:
        _log(f"no iframe offset: {exc}")

    applied = 0
    for c in plan.clicks:
        px = offset_x + c.x
        py = offset_y + c.y
        _log(f"click {c.label} screenshot=({c.x},{c.y}) page=({px:.0f},{py:.0f})")
        page.mouse.click(px, py)
        applied += 1
        page.wait_for_timeout(350)

    for d in plan.drags or []:
        x1, y1 = offset_x + d.x1, offset_y + d.y1
        x2, y2 = offset_x + d.x2, offset_y + d.y2
        _log(
            f"drag {d.label} ({d.x1},{d.y1})->({d.x2},{d.y2}) "
            f"page=({x1:.0f},{y1:.0f})->({x2:.0f},{y2:.0f})"
        )
        page.mouse.move(x1, y1)
        page.mouse.down()
        page.wait_for_timeout(200)
        # stepped move looks more human
        steps = 12
        for i in range(1, steps + 1):
            page.mouse.move(
                x1 + (x2 - x1) * i / steps,
                y1 + (y2 - y1) * i / steps,
            )
            page.wait_for_timeout(30)
        page.mouse.up()
        applied += 1
        page.wait_for_timeout(500)
    return applied


def solve_visible_captcha(
    page,
    *,
    backend: str | None = None,
    max_rounds: int = 5,
) -> bool:
    """
    Loop: detect challenge → screenshot → plan → click → wait.
    Returns True if captcha frame appears solved / disappears.
    """
    backend = backend or os.getenv("VISION_BACKEND") or "auto"
    for round_i in range(1, max_rounds + 1):
        if not captcha_visible(page):
            _log("no captcha visible")
            return True
        _log(f"=== vision round {round_i}/{max_rounds} ===")
        shot = screenshot_challenge(page, tag=f"r{round_i}")
        plan = plan_for_screenshot(shot, backend=backend)
        n_drags = len(plan.drags or [])
        _log(
            f"plan backend={plan.backend} clicks={len(plan.clicks)} drags={n_drags} "
            f"notes={plan.notes!r} instruction={plan.instruction[:120]!r}"
        )
        (DEBUG_DIR / f"vision_plan_r{round_i}.json").write_text(
            json.dumps(
                {
                    "backend": plan.backend,
                    "instruction": plan.instruction,
                    "notes": plan.notes,
                    "clicks": [c.__dict__ for c in plan.clicks],
                    "drags": [d.__dict__ for d in (plan.drags or [])],
                },
                indent=2,
            )
        )
        annotate_plan(shot, plan, tag=f"r{round_i}_ann")
        if not plan.clicks and not (plan.drags or []):
            _log("empty plan — cannot act this round")
            return False
        apply_clicks(page, plan, shot)
        page.wait_for_timeout(2500)
        # Some challenges need an explicit Verify / Next click inside widget —
        # try common buttons on page
        for sel in [
            'button:has-text("Verify")',
            'button:has-text("Next")',
            'div[role="button"]:has-text("Verify")',
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=1000)
                    _log(f"clicked {sel}")
                    page.wait_for_timeout(1500)
            except Exception:
                pass
        shot2 = screenshot_challenge(page, tag=f"r{round_i}_after")
        _log(f"after-click shot {shot2.name}")
        # If checkbox frame shows success, done
        try:
            # challenge iframe often navigates or shrinks when done
            if not captcha_visible(page):
                _log("captcha gone — success")
                return True
        except Exception:
            pass
    _log("max vision rounds exhausted")
    return False
