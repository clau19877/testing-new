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

    if crop_bgr is None or crop_bgr.size == 0:
        return ""
    crop = cv2.resize(crop_bgr, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # White glyph on teal: boost contrast then threshold
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    eq = clahe.apply(gray)
    _, thr = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, thr2 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Prefer dark letter on light background for Tesseract
    candidates = [thr, 255 - thr, thr2, 255 - thr2, eq, gray]
    cfg = "--psm 10 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    votes: dict[str, float] = {}
    for im in candidates:
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

    # If grid OCR missed letters (rotated tiles), fall back to a geometric 3x4 grid
    # and OCR each cell with light rotation attempts.
    if req and len(grid_tiles) < 6:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (70, 40, 40), (110, 255, 255))
        mask[: int(h * 0.32), :] = 0
        mask[int(h * 0.85) :, :] = 0
        ys, xs = np.where(mask > 0)
        if len(xs) > 100:
            x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
            rows, cols = 3, 4
            geom: list[dict[str, Any]] = []
            for r in range(rows):
                for c in range(cols):
                    cx = int(x0 + (c + 0.5) * (x1 - x0) / cols)
                    cy = int(y0 + (r + 0.5) * (y1 - y0) / rows)
                    s = 32
                    crop = img[max(0, cy - s) : cy + s, max(0, cx - s) : cx + s]
                    letter = ""
                    for ang in (0, -15, 15, -10, 10):
                        if crop.size == 0:
                            break
                        M = cv2.getRotationMatrix2D((s, s), ang, 1.0)
                        rot = cv2.warpAffine(crop, M, (s * 2, s * 2), borderMode=cv2.BORDER_REPLICATE)
                        letter = _ocr_single_letter(rot)
                        if letter:
                            break
                    geom.append(
                        {
                            "letter": letter,
                            "cx": cx,
                            "cy": cy,
                            "x": cx - s,
                            "y": cy - s,
                            "w": s * 2,
                            "h": s * 2,
                            "area": s * s * 4,
                        }
                    )
            _log(f"CV geom grid={[ (g['letter'], g['cx'], g['cy']) for g in geom ]}")
            # Prefer geom cells that have letters; merge with existing
            for g in geom:
                if g["letter"]:
                    grid_tiles.append(g)

    clicks: list[ClickTarget] = []
    notes: list[str] = []
    for letter, times in req.items():
        matches = [c for c in grid_tiles if c["letter"] == letter]
        if not matches:
            # Last resort: if only one missing and we know grid geometry, skip
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


def letter_grid_solver_hint(screenshot: Path) -> str:
    """
    Compact CV summary for 2Captcha workers: required letters + grid map.
    Safe to call even when local solving is incomplete.
    """
    img = cv2.imread(str(screenshot))
    if img is None:
        return ""
    h, w = img.shape[:2]
    tiles = _detect_teal_tiles(img)
    if len(tiles) < 4:
        return ""
    med_area = float(np.median([t["area"] for t in tiles]))
    grid_candidates = [t for t in tiles if t["cy"] > h * 0.38 and t["area"] >= med_area * 0.85]
    if len(grid_candidates) < 6:
        grid_candidates = [t for t in tiles if t["cy"] > h * 0.35]
    if not grid_candidates:
        return ""
    grid_top = min(t["y"] for t in grid_candidates) - 8
    req_tiles = [
        t
        for t in tiles
        if t["y"] + t["h"] < grid_top and t["cy"] > h * 0.12 and t["letter"]
    ]
    grid_tiles = [t for t in tiles if t["y"] >= grid_top - 5 and t["letter"]]
    parts: list[str] = []
    if req_tiles:
        req_bits = []
        for t in sorted(req_tiles, key=lambda z: z["cx"]):
            times = _parse_req_times_near(img, t)
            req_bits.append(f"{times}x {t['letter']}")
        parts.append("Required: " + ", ".join(req_bits))
    if grid_tiles:
        # Approximate row grouping for the worker comment
        grid_tiles = sorted(grid_tiles, key=lambda t: (round(t["cy"] / 40), t["cx"]))
        letters = [t["letter"] for t in grid_tiles]
        parts.append("Grid letters L→R, T→B: " + " ".join(letters))
        parts.append(
            "Click ONLY the required letter tiles the listed times; "
            "ignore Skip/Verify."
        )
    return " | ".join(parts)


def plan_drag_cv(screenshot: Path, instruction: str = "") -> VisionPlan | None:
    """
    Locate the draggable tile (astronaut / '+ Move') and destination icon
    (e.g. spaceship) for hCaptcha drag challenges.
    """
    img = cv2.imread(str(screenshot))
    if img is None:
        return None
    import pytesseract

    h, w = img.shape[:2]
    y0, y1 = int(h * 0.11), int(h * 0.88)
    x0, x1 = int(w * 0.01), int(w * 0.99)
    canvas = img[y0:y1, x0:x1]
    ch, cw = canvas.shape[:2]
    hsv = cv2.cvtColor(canvas, cv2.COLOR_BGR2HSV)

    def boxes_from_mask(
        mask: np.ndarray,
        min_a: int = 2500,
        max_a: int = 25000,
        morph: bool = True,
    ) -> list[dict]:
        m = mask
        if morph:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out: list[dict[str, Any]] = []
        for c in contours:
            x, y, ww, hh = cv2.boundingRect(c)
            area = ww * hh
            if not (min_a < area < max_a):
                continue
            ar = ww / max(hh, 1)
            if not (0.55 < ar < 1.8):
                continue
            out.append(
                {
                    "x": x,
                    "y": y,
                    "w": ww,
                    "h": hh,
                    "cx": x + ww // 2,
                    "cy": y + hh // 2,
                    "area": area,
                }
            )
        return out

    # Lavender + pink/magenta selection tiles (astronaut highlight varies)
    lav = cv2.inRange(hsv, np.array((115, 15, 70)), np.array((155, 140, 230)))
    pink = cv2.inRange(hsv, np.array((140, 30, 80)), np.array((179, 200, 255)))
    # Pink: avoid heavy morph (merges astronaut into nebula)
    boxes = boxes_from_mask(lav) + boxes_from_mask(pink, min_a=2000, max_a=12000, morph=False)
    # de-dupe overlapping
    uniq: list[dict[str, Any]] = []
    for b in sorted(boxes, key=lambda z: -z["area"]):
        if any(abs(b["cx"] - u["cx"]) < 25 and abs(b["cy"] - u["cy"]) < 25 for u in uniq):
            continue
        uniq.append(b)
    boxes = uniq

    for b in boxes:
        ay0 = max(0, b["y"] - 40)
        strip = canvas[ay0 : b["y"] + 8, max(0, b["x"] - 25) : b["x"] + b["w"] + 25]
        t = ""
        if strip.size:
            strip2 = cv2.resize(strip, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            t = pytesseract.image_to_string(strip2).strip().lower()
        b["above"] = t
        # require a real "move" token (avoid random OCR noise)
        b["is_move"] = bool(re.search(r"\bmov", t)) or "move" in t.replace(" ", "")

    move_boxes = [b for b in boxes if b.get("is_move")]
    if not move_boxes and boxes:
        # Prefer mid-canvas square selection tiles (~60-90px), not nebula blobs
        mid = [
            b
            for b in boxes
            if cw * 0.15 < b["cx"] < cw * 0.85
            and ch * 0.2 < b["cy"] < ch * 0.7
            and 40 <= b["w"] <= 100
            and 40 <= b["h"] <= 110
            and 3000 <= b["area"] <= 12000
        ]
        pool = mid or [
            b
            for b in boxes
            if cw * 0.2 < b["cx"] < cw * 0.8 and ch * 0.15 < b["cy"] < ch * 0.75
        ] or boxes
        move_boxes = [
            sorted(
                pool,
                key=lambda z: abs(z["cx"] - cw / 2) + abs(z["cy"] - ch / 2) - z["area"] * 0.01,
            )[0]
        ]

    if not move_boxes:
        _log("drag CV: no source tile")
        return None
    src = move_boxes[0]

    # White line-art destination icons
    gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    white = (gray > 185).astype(np.uint8) * 255
    n, _labels, stats, cents = cv2.connectedComponentsWithStats(white, 8)
    icons: list[dict[str, Any]] = []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if area < 80 or area > 4000 or ww < 12 or hh < 12:
            continue
        cx, cy = int(cents[i][0]), int(cents[i][1])
        if abs(cx - src["cx"]) < 55 and abs(cy - src["cy"]) < 55:
            continue
        # bbox area favors outlined icons (spaceship) over thin stroke fragments
        bbox_a = int(ww * hh)
        icons.append(
            {
                "cx": cx,
                "cy": cy,
                "area": int(area),
                "bbox_a": bbox_a,
                "x": x,
                "y": y,
                "w": ww,
                "h": hh,
            }
        )

    dst = None
    lower_i = (instruction or "").lower()
    if icons:
        ranked = sorted(icons, key=lambda z: (-z["bbox_a"], -z["area"]))
        if "spaceship" in lower_i or "ship" in lower_i or "rocket" in lower_i:
            dst = ranked[0]
        else:
            dst = ranked[0]

    # Also consider other selection boxes as dest candidates
    others = [
        b
        for b in boxes
        if abs(b["cx"] - src["cx"]) > 50 or abs(b["cy"] - src["cy"]) > 50
    ]
    if others and (dst is None or dst.get("bbox_a", dst.get("area", 0)) < 1500):
        cand = sorted(others, key=lambda z: -z["area"])[0]
        # Prefer white-icon dest when it has a solid bbox; else other box
        if dst is None:
            dst = {"cx": cand["cx"], "cy": cand["cy"], "area": cand["area"], "bbox_a": cand["area"]}
        elif cand["area"] > dst.get("bbox_a", 0) * 1.2:
            dst = {"cx": cand["cx"], "cy": cand["cy"], "area": cand["area"], "bbox_a": cand["area"]}

    if dst is None:
        _log("drag CV: no destination")
        return None

    sx = int(src["cx"] + x0)
    # Prefer the "+ Move" handle near the top of the selection tile
    sy = int(src["y"] + y0 + max(8, int(src["h"] * 0.15)))
    dx = int(dst["cx"] + x0)
    dy = int(dst["cy"] + y0)
    dist = ((sx - dx) ** 2 + (sy - dy) ** 2) ** 0.5
    if dist < 60:
        _log(f"drag CV: src/dest too close ({dist:.0f}px) — rejecting")
        return None
    _log(
        f"drag CV src=({sx},{sy}) → dest=({dx},{dy}) "
        f"boxes={len(boxes)} icons={len(icons)} move_ocr={src.get('is_move')}"
    )
    return VisionPlan(
        instruction=(instruction or "drag")[:300],
        clicks=[],
        drags=[DragTarget(x1=sx, y1=sy, x2=dx, y2=dy, label="move->target")],
        backend="ocr",
        notes="drag CV pink/Move + white icon dest",
    )


def plan_ocr(screenshot: Path) -> VisionPlan:
    text, boxes = _ocr_image(screenshot)
    _log(f"OCR text:\n{text}")
    img = Image.open(screenshot)
    w, h = img.size
    lower = text.lower()

    if challenge_image_blank(screenshot):
        _log("blank challenge canvas — refusing heuristic plan")
        return VisionPlan(
            instruction=text.strip()[:300],
            clicks=[],
            backend="ocr",
            notes="blank canvas",
        )

    # Drag-style challenges (common on Riot / hCaptcha enterprise)
    if "drag" in lower:
        cv_drag = plan_drag_cv(screenshot, instruction=text.strip())
        if cv_drag and cv_drag.drags:
            return cv_drag
        # No blind heuristic — empty plan lets hybrid escalate to 2Captcha
        _log("drag CV missed — empty plan (no heuristic)")
        return VisionPlan(
            instruction=text.strip()[:300],
            clicks=[],
            backend="ocr",
            notes="drag CV miss",
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
        # Prefer hybrid: local CV first, 2Captcha Coordinates as oracle/fallback
        if os.getenv("TWOCAPTCHA_API_KEY") or os.getenv("TWO_CAPTCHA_API_KEY"):
            backend = "hybrid"
        elif os.getenv("OPENAI_API_KEY"):
            backend = "openai"
        else:
            backend = "ocr"
    _log(f"planning with backend={backend} image={screenshot}")
    if backend in (
        "twocaptcha",
        "2captcha",
        "2cap",
        "2cap_click",
        "hybrid",
        "yescaptcha",
        "yes",
    ):
        # hybrid → local CV first; then YesCaptcha Classification; then 2Captcha
        if backend == "hybrid":
            local_first = True
        else:
            local_first = os.getenv("VISION_LOCAL_FIRST", "0") in ("1", "true", "True")
        if local_first:
            if challenge_image_blank(screenshot) or not challenge_content_ready(screenshot):
                _log("blank/loading challenge image — skip local CV, use remote solvers")
            else:
                local = plan_ocr(screenshot)
                # Incomplete letter OCR (e.g. 1 of 2 letters) is worse than 2cap
                notes_l = (local.notes or "").lower()
                instr_l = (local.instruction or "").lower()
                incomplete = "not found" in notes_l or "miss" in notes_l
                if ("letter" in instr_l or "click each" in instr_l) and local.clicks:
                    # Prefer escalating when we clicked fewer times than Nx sum
                    nx = [int(n) for n in re.findall(r"(\d)\s*[xX]", local.instruction or "")]
                    need = sum(nx) if nx else 0
                    if need and len(local.clicks) < need:
                        incomplete = True
                used_as_hint = False
                if (local.clicks or (local.drags or [])) and not incomplete:
                    # Letter-grid OCR is often wrong on rotated tiles; prefer remote
                    # with a CV hint unless VISION_LETTER_TRUST_LOCAL=1.
                    prefer_2cap_letters = os.getenv(
                        "VISION_LETTER_TRUST_LOCAL", "0"
                    ) not in ("1", "true", "True")
                    is_letter = (
                        "letter" in instr_l
                        or "click each" in instr_l
                        or "teal-tile" in notes_l
                    )
                    if prefer_2cap_letters and is_letter and local.clicks:
                        used_as_hint = True
                        _log(
                            "local letter-grid plan held as hint — "
                            "preferring remote click solvers"
                        )
                    else:
                        _log(
                            f"local CV hit backend={local.backend} "
                            f"clicks={len(local.clicks)} drags={len(local.drags or [])}"
                        )
                        return local
                if incomplete:
                    _log(f"local CV incomplete ({local.notes}) — escalating to remote")
                elif not used_as_hint:
                    _log("local CV miss — escalating to remote click solvers")

        # Don't waste solver budget on Riot error chrome / login form screenshots
        try:
            from PIL import Image as _PILImage
            import pytesseract

            w, h = _PILImage.open(screenshot).size
            probe = pytesseract.image_to_string(_PILImage.open(screenshot)).lower()
            bad_markers = (
                "invalid",
                "facebook",
                "google",
                "xbox",
                "playstation",
                "stay signed in",
                "captcha selection",
            )
            looks_login = (
                ("sign in" in probe or "username" in probe or "password" in probe)
                and "letter" not in probe
                and "drag" not in probe
                and "click each" not in probe
                and "click on" not in probe
            )
            if looks_login or any(m in probe for m in bad_markers):
                _log("screenshot looks like login/error page — empty plan")
                return VisionPlan(
                    instruction="invalid/error page",
                    clicks=[],
                    backend=backend,
                    notes="refusing remote solver on non-challenge screenshot",
                )
            if w > 700 and h < 500:
                _log("screenshot aspect looks non-challenge — empty plan")
                return VisionPlan(
                    instruction="non-challenge",
                    clicks=[],
                    backend=backend,
                    notes="refusing remote solver on odd screenshot",
                )
        except Exception:
            pass

        extra = ""
        try:
            extra = letter_grid_solver_hint(screenshot)
            if extra:
                _log(f"letter-grid hint: {extra[:160]}")
        except Exception as exc:
            _log(f"letter-grid hint skipped: {exc}")

        # Screenshot-mode YesCaptcha (drag / point / new styles without .task-image).
        # Classic 3x3 grids are handled earlier via try_solve_task_grid().
        use_yes = bool(
            (
                os.getenv("YESCAPTCHA_API_KEY")
                or os.getenv("YES_CAPTCHA_API_KEY")
                or ""
            ).strip()
        ) and backend in ("hybrid", "yescaptcha", "yes", "auto")
        if backend in ("yescaptcha", "yes"):
            use_yes = True
        if use_yes:
            try:
                from yescaptcha_click import plan_yescaptcha_clicks

                plan = plan_yescaptcha_clicks(screenshot, extra_comment=extra or None)
                if plan.clicks or (plan.drags or []):
                    return plan
                _log(f"YesCaptcha empty plan ({plan.notes}) — trying 2Captcha")
            except Exception as exc:
                _log(f"YesCaptcha failed ({exc}) — falling back to 2Captcha")

        if backend in ("yescaptcha", "yes") and not (
            os.getenv("TWOCAPTCHA_API_KEY")
            or os.getenv("TWO_CAPTCHA_API_KEY")
            or os.getenv("2CAPTCHA_API_KEY")
        ):
            return VisionPlan(
                instruction="yescaptcha miss",
                clicks=[],
                backend="yescaptcha",
                notes="YesCaptcha returned no actions and no 2Captcha fallback key",
            )

        from twocaptcha_click import plan_twocaptcha_clicks

        return plan_twocaptcha_clicks(screenshot, extra_comment=extra or None)
    if backend == "ocr":
        plan = plan_ocr(screenshot)
        # Optional agent fallback when local CV/OCR cannot produce actions
        if (
            not plan.clicks
            and not (plan.drags or [])
            and os.getenv("VISION_AGENT_FALLBACK", "1") not in ("0", "false", "False")
        ):
            _log("OCR/CV empty — falling back to agent vision")
            try:
                return plan_agent(
                    screenshot,
                    timeout=float(os.getenv("VISION_AGENT_TIMEOUT") or "180"),
                )
            except Exception as exc:
                _log(f"agent fallback failed: {exc}")
                return plan
        # Optional 2Captcha click fallback after empty/weak OCR
        if (
            not plan.clicks
            and not (plan.drags or [])
            and (os.getenv("TWOCAPTCHA_API_KEY") or os.getenv("TWO_CAPTCHA_API_KEY"))
            and os.getenv("VISION_2CAP_FALLBACK", "1") not in ("0", "false", "False")
        ):
            _log("OCR/CV empty — falling back to 2Captcha Coordinates")
            try:
                from twocaptcha_click import plan_twocaptcha_clicks

                return plan_twocaptcha_clicks(screenshot)
            except Exception as exc:
                _log(f"2Captcha click fallback failed: {exc}")
                return plan
        return plan
    if backend == "openai":
        return plan_openai(screenshot)
    if backend == "agent":
        return plan_agent(
            screenshot,
            timeout=float(os.getenv("VISION_AGENT_TIMEOUT") or "180"),
        )
    raise ValueError(f"unknown VISION_BACKEND: {backend}")


# ---------------------------------------------------------------------------
# Playwright integration
# ---------------------------------------------------------------------------

def find_hcaptcha_frame(page):
    """Return the Playwright frame that hosts the hCaptcha challenge (not checkbox)."""
    challenge = []
    checkbox = []
    other = []
    for frame in page.frames:
        url = (frame.url or "").lower()
        if "hcaptcha.com" not in url and "hcaptcha" not in url:
            continue
        if "frame=checkbox" in url or ("checkbox" in url and "challenge" not in url):
            checkbox.append(frame)
        elif "frame=challenge" in url or "/challenge" in url:
            challenge.append(frame)
        elif "frame=" in url or "challenge" in url:
            challenge.append(frame)
        else:
            other.append(frame)

    def _score(frame) -> tuple:
        # Prefer larger challenge documents (checkbox is ~300x75).
        try:
            box = frame.locator("body").bounding_box(timeout=800)
        except Exception:
            box = None
        area = (box["width"] * box["height"]) if box else 0
        return (area, )

    if challenge:
        return max(challenge, key=_score)
    if other:
        return max(other, key=_score)
    # Last resort: never use tiny checkbox for letter-grid clicks if avoidable
    return checkbox[0] if checkbox else None


def find_challenge_iframe_locator(page):
    """Locator for the challenge-sized hCaptcha iframe element on the page."""
    frames = page.locator('iframe[src*="hcaptcha.com"]')
    best = None
    best_area = 0
    try:
        n = frames.count()
    except Exception:
        return None
    for i in range(n):
        fr = frames.nth(i)
        try:
            src = (fr.get_attribute("src") or "").lower()
        except Exception:
            src = ""
        try:
            box = fr.bounding_box(timeout=1000)
        except Exception:
            box = None
        area = (box["width"] * box["height"]) if box else 0
        is_challenge = "frame=challenge" in src or "/challenge" in src or area >= 300 * 300
        if is_challenge and area >= best_area:
            best = fr
            best_area = area
    return best


def captcha_visible(page) -> bool:
    try:
        frames = page.locator('iframe[src*="hcaptcha.com"]')
        n = frames.count()
        for i in range(n):
            fr = frames.nth(i)
            box = fr.bounding_box(timeout=1000)
            # Visible checkbox/challenge
            if box and box["width"] > 20 and box["height"] > 20 and box["y"] >= -5:
                return True
            # Off-screen but still a challenge-sized frame → captcha not done
            if box and box["width"] >= 300 and box["height"] >= 300:
                return True
            src = ""
            try:
                src = (fr.get_attribute("src") or "").lower()
            except Exception:
                pass
            if "frame=challenge" in src or "/challenge" in src:
                return True
    except Exception:
        pass
    try:
        content = page.content().lower()
    except Exception:
        content = ""
    return "click each letter" in content or "please drag the" in content


def challenge_image_blank(path: Path) -> bool:
    """True when the challenge canvas failed to load (white/empty mid region)."""
    try:
        from PIL import Image

        im = Image.open(path).convert("RGB")
        w, h = im.size
        mid = im.crop((int(w * 0.05), int(h * 0.18), int(w * 0.95), int(h * 0.82)))
        pixels = list(mid.getdata())
        if not pixels:
            return True
        mean = sum(sum(p) for p in pixels) / (len(pixels) * 3)
        # Nearly-white mid canvas with little structure
        whiteish = sum(1 for p in pixels if p[0] > 235 and p[1] > 235 and p[2] > 235)
        white_ratio = whiteish / len(pixels)
        return mean > 230 and white_ratio > 0.85
    except Exception:
        return False


def challenge_content_ready(path: Path) -> bool:
    """
    True when mid-canvas has real challenge content (not blank / spinner).
    Letter grids → teal tiles; drag → dark structured scene.
    """
    try:
        import numpy as np
        from PIL import Image

        if challenge_image_blank(path):
            return False
        im = np.array(Image.open(path).convert("RGB"))
        h, w = im.shape[:2]
        mid = im[int(h * 0.18) : int(h * 0.82), int(w * 0.04) : int(w * 0.96)]
        mean = float(mid.mean())
        std = float(mid.std())
        # Loading spinner on pale gray
        if mean > 200 and std < 40:
            return False
        r, g, b = mid[:, :, 0], mid[:, :, 1], mid[:, :, 2]
        teal = ((g > r + 15) & (g > b + 10) & (g > 80)).mean()
        dark = (mid.mean(axis=2) < 80).mean()
        if teal > 0.08:
            return True
        if dark > 0.35 and std > 40:
            return True
        return std > 45
    except Exception:
        return not challenge_image_blank(path)


def wait_for_challenge_canvas(page, timeout_s: float = 15.0) -> None:
    """Poll until the challenge mid-area has loaded image content (not blank/spinner)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        tmp = DEBUG_DIR / f"_canvas_probe_{int(time.time() * 1000)}.png"
        ensure_debug()
        loc = page.locator('iframe[src*="hcaptcha.com"]').last
        try:
            if loc.count() > 0:
                box = loc.bounding_box(timeout=2000)
                if box and box["width"] > 50 and box["y"] >= 0:
                    page.screenshot(path=str(tmp), clip=box)
                    if challenge_content_ready(tmp):
                        _log("challenge canvas ready")
                        try:
                            tmp.unlink(missing_ok=True)
                        except Exception:
                            pass
                        return
                    _log("challenge canvas still loading — waiting…")
        except Exception as exc:
            _log(f"canvas probe: {exc}")
        page.wait_for_timeout(800)
    _log("challenge canvas wait timed out — proceeding anyway")


def challenge_still_solvable(path: Path) -> bool:
    """True when Verify may be showing but letter/drag content is still interactive."""
    return challenge_content_ready(path)

def screenshot_challenge(page, tag: str = "challenge") -> Path:
    """Screenshot the challenge area (prefer challenge iframe bbox)."""
    ensure_debug()
    # Drag/grid assets often load after the prompt banner — wait for content
    if "after" not in tag and "peek" not in tag:
        wait_for_challenge_canvas(page)
    # If the challenge iframe was parked off-screen, bring it back before clip
    ensure_challenge_iframe_on_screen(page)
    path = DEBUG_DIR / f"vision_{tag}_{int(time.time())}.png"
    # Try to clip to the challenge iframe
    loc = page.locator('iframe[src*="hcaptcha.com"]').last
    try:
        if loc.count() > 0:
            box = loc.bounding_box(timeout=3000)
            if box and box["width"] > 50 and box["y"] >= 0 and box["x"] >= -20:
                page.screenshot(path=str(path), clip=box)
                _save_clip(path, box)
                _log(f"clipped iframe screenshot → {path.name} {box}")
                return path
    except Exception as exc:
        _log(f"iframe clip failed: {exc}")
    page.screenshot(path=str(path), full_page=False)
    _save_clip(path, None)
    _log(f"full viewport screenshot → {path.name}")
    return path


def _clip_sidecar(screenshot: Path) -> Path:
    return screenshot.with_suffix(screenshot.suffix + ".clip.json")


def _save_clip(screenshot: Path, box: dict | None) -> None:
    import json as _json

    (_clip_sidecar(screenshot)).write_text(_json.dumps(box))


def _load_clip(screenshot: Path) -> dict | None:
    import json as _json

    side = _clip_sidecar(screenshot)
    if not side.is_file():
        return None
    try:
        data = _json.loads(side.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def ensure_challenge_iframe_on_screen(page) -> bool:
    """
    hCaptcha parks the challenge iframe at y=-9999 after some interactions.
    Force it back on-screen so screenshots/clicks stay aligned.
    """
    try:
        moved = page.evaluate(
            """() => {
              const frames = [...document.querySelectorAll('iframe[src*="hcaptcha.com"]')];
              let fixed = 0;
              for (const f of frames) {
                const r = f.getBoundingClientRect();
                const big = (f.clientWidth || r.width) >= 300 && (f.clientHeight || r.height) >= 300;
                if (!big) continue;
                if (r.top < 0 || r.left < -50 || r.top > window.innerHeight) {
                  f.style.setProperty('position', 'fixed', 'important');
                  f.style.setProperty('left', '50%', 'important');
                  f.style.setProperty('top', '80px', 'important');
                  f.style.setProperty('transform', 'translateX(-50%)', 'important');
                  f.style.setProperty('z-index', '2147483646', 'important');
                  f.style.setProperty('opacity', '1', 'important');
                  f.style.setProperty('pointer-events', 'auto', 'important');
                  fixed += 1;
                }
              }
              return fixed;
            }"""
        )
        if moved:
            _log(f"repositioned {moved} off-screen hCaptcha iframe(s)")
            page.wait_for_timeout(400)
            shield_social_login_buttons(page)
            return True
    except Exception as exc:
        _log(f"iframe reposition failed: {exc}")
    return False


def shield_social_login_buttons(page) -> int:
    """
    Disable pointer events on Riot social OAuth buttons so captcha clicks
    cannot fall through to Facebook / Apple / Xbox / PlayStation.
    """
    try:
        n = page.evaluate(
            """() => {
              const needles = [
                'facebook', 'google', 'apple', 'xbox', 'playstation', 'sony',
                'live.com', 'microsoft', 'steam', 'twitter', 'discord'
              ];
              let hit = 0;
              const nodes = [
                ...document.querySelectorAll('a, button, [role="button"], div[tabindex]')
              ];
              for (const el of nodes) {
                const blob = (
                  (el.getAttribute('href') || '') + ' ' +
                  (el.getAttribute('aria-label') || '') + ' ' +
                  (el.getAttribute('data-testid') || '') + ' ' +
                  (el.id || '') + ' ' +
                  (el.className || '') + ' ' +
                  (el.innerText || '')
                ).toLowerCase();
                if (!needles.some((n) => blob.includes(n))) continue;
                // Never shield hCaptcha controls
                if (blob.includes('hcaptcha') || blob.includes('challenge')) continue;
                el.style.setProperty('pointer-events', 'none', 'important');
                el.setAttribute('data-riot-social-shielded', '1');
                hit += 1;
              }
              return hit;
            }"""
        )
        if n:
            _log(f"shielded {n} social login controls (pointer-events:none)")
        return int(n or 0)
    except Exception as exc:
        _log(f"social shield failed: {exc}")
        return 0


def click_point_is_on_hcaptcha(page, x: float, y: float) -> bool:
    """True when document.elementFromPoint is the hCaptcha iframe (or inside it)."""
    try:
        return bool(
            page.evaluate(
                """([x, y]) => {
                  const el = document.elementFromPoint(x, y);
                  if (!el) return false;
                  if (el.tagName === 'IFRAME') {
                    const src = (el.getAttribute('src') || '').toLowerCase();
                    return src.includes('hcaptcha');
                  }
                  // After CSS reposition, a wrapper/overlay can sit above the
                  // iframe in hit-testing — still accept nearby hCaptcha iframes.
                  const frames = [...document.querySelectorAll('iframe[src*="hcaptcha.com"]')];
                  for (const f of frames) {
                    const r = f.getBoundingClientRect();
                    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
                        && r.width >= 300 && r.height >= 300) {
                      return true;
                    }
                  }
                  return false;
                }""",
                [x, y],
            )
        )
    except Exception:
        return False


def verify_button_visible(page) -> bool:
    """True when the challenge shows an in-iframe Verify / Next control."""
    frame = find_hcaptcha_frame(page)
    if not frame:
        return False
    for sel in [
        'div.button-submit:has-text("Verify")',
        '.button-submit:has-text("Verify")',
        'button:has-text("Verify")',
        'div.button-submit:has-text("Next")',
        '.button-submit:has-text("Next")',
    ]:
        try:
            loc = frame.locator(sel).first
            if loc.count() and loc.is_visible():
                txt = (loc.inner_text(timeout=400) or "").strip().lower()
                if "skip" in txt:
                    continue
                return True
        except Exception:
            continue
    return False


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
    Apply click/drag plans. For clipped challenge screenshots, always click
    inside the hCaptcha frame (screenshot coords == frame content coords).
    Page-level mouse + bbox offsets are unreliable after hCaptcha parks the
    iframe at y=-9999 and our CSS reposition — those mis-hits Riot social
    login buttons (Facebook / Apple / Xbox / PlayStation).
    """
    actions = len(plan.clicks) + len(plan.drags or [])
    if actions == 0:
        _log("no clicks/drags in plan")
        return 0

    ensure_challenge_iframe_on_screen(page)

    clipped = False
    saved = _load_clip(screenshot)
    if saved and saved.get("width", 0) > 50:
        clipped = True
    else:
        try:
            from PIL import Image as _PILImage

            sw, sh = _PILImage.open(screenshot).size
            clipped = sw <= 600 and sh <= 650
        except Exception:
            clipped = True

    if clipped:
        _log("clipped challenge shot — using frame-local clicks")
        return _apply_clicks_in_frame(page, plan)

    _log("full-page screenshot — clicks are page coords")
    applied = 0
    for c in plan.clicks:
        if c.y < 0 or c.x < 0:
            continue
        _log(f"click {c.label} page=({c.x},{c.y})")
        page.mouse.click(c.x, c.y)
        applied += 1
        page.wait_for_timeout(350)
    for d in plan.drags or []:
        if min(d.x1, d.y1, d.x2, d.y2) < 0:
            continue
        _log(f"drag {d.label} ({d.x1},{d.y1})->({d.x2},{d.y2})")
        page.mouse.move(d.x1, d.y1)
        page.wait_for_timeout(200)
        page.mouse.down()
        page.wait_for_timeout(450)
        steps = 28
        for i in range(1, steps + 1):
            page.mouse.move(
                d.x1 + (d.x2 - d.x1) * i / steps,
                d.y1 + (d.y2 - d.y1) * i / steps,
                steps=1,
            )
            page.wait_for_timeout(30)
        page.wait_for_timeout(350)
        page.mouse.up()
        applied += 1
        page.wait_for_timeout(1000)
    return applied


def _apply_clicks_in_frame(page, plan: VisionPlan) -> int:
    """
    Click inside the challenge iframe using iframe-relative coordinates.
    Prefer Playwright iframe.click(position=…) so CSS reposition / overlays
    cannot make elementFromPoint falsely reject in-bounds challenge clicks.
    """
    ensure_challenge_iframe_on_screen(page)
    iframe = find_challenge_iframe_locator(page)
    frame = find_hcaptcha_frame(page)
    if frame:
        _log(f"frame-local target url={(frame.url or '')[:90]}")
    box = None
    if iframe is not None:
        try:
            box = iframe.bounding_box(timeout=3000)
        except Exception:
            box = None
    if (not box or box["width"] < 200) and frame is not None:
        try:
            box = frame.locator("body").bounding_box(timeout=2000)
        except Exception:
            box = None
    if not box or box["width"] < 200 or box["height"] < 200:
        _log(f"frame-local clicks: no challenge-sized box (got {box})")
        return 0

    shield_social_login_buttons(page)
    _log(
        f"frame-local box x={box['x']:.0f} y={box['y']:.0f} "
        f"w={box['width']:.0f} h={box['height']:.0f}"
    )
    applied = 0
    use_iframe_click = iframe is not None
    try:
        for c in plan.clicks:
            if c.x < 0 or c.y < 0 or c.x > box["width"] + 5 or c.y > box["height"] + 5:
                _log(f"skip out-of-frame click {c.label} ({c.x},{c.y})")
                continue
            px = box["x"] + c.x
            py = box["y"] + c.y
            # Soft safety: only skip when clearly outside every challenge iframe.
            if not click_point_is_on_hcaptcha(page, px, py):
                _log(
                    f"warn click {c.label} page=({px:.0f},{py:.0f}) — "
                    "elementFromPoint miss; still clicking via iframe position"
                )
            clicked = False
            if use_iframe_click:
                try:
                    iframe.click(
                        position={"x": float(c.x), "y": float(c.y)},
                        timeout=2500,
                        force=True,
                    )
                    clicked = True
                except Exception as exc:
                    _log(f"iframe.click failed ({exc}); falling back to page mouse")
            if not clicked:
                page.mouse.click(px, py)
            _log(f"frame click {c.label} local=({c.x},{c.y}) page=({px:.0f},{py:.0f})")
            applied += 1
            page.wait_for_timeout(350)
            if page_looks_social_oauth(page):
                _log("abort clicks — navigated to social OAuth")
                return applied
        for d in plan.drags or []:
            if min(d.x1, d.y1, d.x2, d.y2) < 0:
                continue
            if max(d.x1, d.x2) > box["width"] + 5 or max(d.y1, d.y2) > box["height"] + 5:
                _log(f"skip out-of-frame drag {d.label}")
                continue
            sx, sy = box["x"] + d.x1, box["y"] + d.y1
            _log(f"frame drag {d.label} ({d.x1},{d.y1})->({d.x2},{d.y2})")
            page.mouse.move(sx, sy)
            page.wait_for_timeout(200)
            page.mouse.down()
            page.wait_for_timeout(300)
            steps = 24
            for i in range(1, steps + 1):
                page.mouse.move(
                    box["x"] + d.x1 + (d.x2 - d.x1) * i / steps,
                    box["y"] + d.y1 + (d.y2 - d.y1) * i / steps,
                )
                page.wait_for_timeout(25)
            page.wait_for_timeout(200)
            page.mouse.up()
            applied += 1
            page.wait_for_timeout(800)
            if page_looks_social_oauth(page):
                _log("abort drag — navigated to social OAuth")
                return applied
    except Exception as exc:
        _log(f"frame-local clicks failed: {exc}")
    return applied


def click_challenge_next(page) -> bool:
    """Click Next/Verify inside the hCaptcha challenge iframe when present."""
    frame = find_hcaptcha_frame(page)
    if not frame:
        return False
    for sel in [
        'div.button-submit:has-text("Verify")',
        '.button-submit:has-text("Verify")',
        'button:has-text("Verify")',
        'div[role="button"]:has-text("Verify")',
        'div.button-submit:has-text("Next")',
        '.button-submit:has-text("Next")',
        'button:has-text("Next")',
        'div[role="button"]:has-text("Next")',
    ]:
        try:
            loc = frame.locator(sel).first
            if loc.count() and loc.is_visible():
                txt = (loc.inner_text(timeout=500) or "").strip().lower()
                aria = (loc.get_attribute("aria-label") or "").strip().lower()
                if "skip" in txt or "skip" in aria:
                    continue
                # Prefer normal click; force only if covered after iframe CSS nudge.
                try:
                    loc.click(timeout=2500)
                except Exception:
                    loc.click(timeout=2500, force=True)
                _log(f"challenge Next/Verify via frame {sel}")
                return True
        except Exception:
            continue
    _log("challenge Next/Verify button not clickable in frame")
    return False


def page_has_invalid_captcha(page) -> bool:
    try:
        body = page.content().lower()
    except Exception:
        return False
    return (
        "captcha selection was invalid" in body
        or "captcha attempt has timed out" in body
        or "please try again" in body
        and "captcha" in body
    )


def page_has_riot_oops(page) -> bool:
    try:
        body = page.content().lower()
    except Exception:
        return False
    return "something went wrong" in body or "captcha attempt has timed out" in body


def page_looks_mfa(page) -> bool:
    try:
        body = page.content().lower()
    except Exception:
        body = ""
    if page.locator('input[autocomplete="one-time-code"]').count():
        return True
    return any(
        s in body
        for s in (
            "enter the code",
            "verification code",
            "multifactor",
            "check your email",
            "email code",
        )
    )


def page_looks_auth_progress(page) -> bool:
    """True when captcha is done and login advanced (account page or MFA)."""
    if page_looks_past_login(page):
        return True
    if page_has_riot_oops(page):
        return False
    return page_looks_mfa(page)


def solve_visible_captcha(
    page,
    *,
    backend: str | None = None,
    max_rounds: int = 5,
) -> bool:
    """
    Loop: detect challenge → screenshot → plan → click → wait.
    Returns True if captcha frame appears solved / disappears into MFA or account.
    """
    backend = backend or os.getenv("VISION_BACKEND") or "auto"
    prev_instruction = ""
    # Wait up to ~8s for the challenge iframe to appear after sign-in
    for _ in range(16):
        if captcha_visible(page):
            break
        page.wait_for_timeout(500)
    shield_social_login_buttons(page)
    for round_i in range(1, max_rounds + 1):
        if page_has_riot_oops(page):
            _log("Riot Oops page — captcha path failed")
            return False
        if page_has_invalid_captcha(page):
            _log("CAPTCHA selection invalid — fail this session")
            return False
        if not captcha_visible(page):
            # Give Riot a moment to navigate to MFA / account / Oops
            page.wait_for_timeout(2500)
            if page_has_riot_oops(page) or page_has_invalid_captcha(page):
                _log("Riot Oops/invalid after captcha disappeared — fail")
                return False
            if page_looks_auth_progress(page):
                _log("captcha cleared — MFA/account progress")
                return True
            # If we landed on a social OAuth page, that was a misclick — fail
            if page_looks_social_oauth(page):
                _log(f"misclick navigated to social login ({(page.url or '')[:80]}) — fail")
                return False
            _log("no captcha visible (still on login form) — treating as solved")
            return True
        _log(f"=== vision round {round_i}/{max_rounds} ===")
        # YesCaptcha DEMO / new-style path (prefer over screenshot OCR):
        # classic 9× .task-image → objects[]; Riot Enterprise → canvas export
        # + anchors → box clicks. Docs:
        # https://yescaptcha.atlassian.net/wiki/spaces/YESCAPTCHA/pages/30113813
        yes_key = (
            os.getenv("YESCAPTCHA_API_KEY")
            or os.getenv("YES_CAPTCHA_API_KEY")
            or ""
        ).strip()
        if yes_key and backend in ("hybrid", "auto", "yescaptcha", "yes"):
            try:
                from yescaptcha_click import try_solve_task_grid

                grid_result = try_solve_task_grid(page)
            except Exception as exc:
                _log(f"YesCaptcha DEMO/canvas path error: {exc}")
                grid_result = None
            if grid_result is True:
                page.wait_for_timeout(1500)
                if page_has_riot_oops(page) or page_has_invalid_captcha(page):
                    _log("Oops/invalid after YesCaptcha solve — fail")
                    return False
                if page_looks_auth_progress(page) or not captcha_visible(page):
                    _log("YesCaptcha DEMO/canvas cleared captcha")
                    return True
                prev_instruction = "yescaptcha-demo"
                continue
            if grid_result is False:
                # DEMO retries when checkbox not checked yet.
                # After two incomplete rounds, fall through to 2Captcha/coords.
                prev_instruction = "yescaptcha-demo"
                if page_has_riot_oops(page) or page_has_invalid_captcha(page):
                    _log("Oops/invalid after YesCaptcha round — fail")
                    return False
                if page_looks_auth_progress(page):
                    return True
                if round_i < 2:
                    _log("YesCaptcha DEMO/canvas incomplete — retry")
                    continue
                _log(
                    "YesCaptcha DEMO/canvas still incomplete — "
                    "falling through to coordinate solvers"
                )
            # grid_result is None → no grid/canvas; use screenshot path
        # Verify may appear early while the letter grid is still unsolved — peek first
        if verify_button_visible(page):
            peek = screenshot_challenge(page, tag=f"r{round_i}_peek")
            if challenge_still_solvable(peek):
                _log("Verify visible but challenge content still present — re-solving")
                shot = peek
            else:
                _log("Verify/Next already visible — clicking instead of re-solving")
                if click_challenge_next(page):
                    page.wait_for_timeout(2000)
                    if page_has_riot_oops(page):
                        _log("Oops after Verify — fail")
                        return False
                    if not captcha_visible(page) and page_looks_auth_progress(page):
                        _log("captcha gone after Verify — success")
                        return True
                    if not captcha_visible(page):
                        _log("captcha cleared after Verify (still on auth page)")
                        # wait briefly for Oops/MFA
                        page.wait_for_timeout(2000)
                        if page_has_riot_oops(page):
                            return False
                        return True
                    # Verify didn't clear — fall through to a fresh solve next loop
                    _log("Verify click did not clear — will re-solve next round")
                    continue
                shot = peek
        else:
            shot = screenshot_challenge(page, tag=f"r{round_i}")
        # If screenshot is a full login page (no challenge clip), wait and retry
        try:
            from PIL import Image as _PILImage

            sw, sh = _PILImage.open(shot).size
            if sw > 700 and not prev_instruction:
                _log("screenshot looks like full page — waiting for challenge widget")
                page.wait_for_timeout(2000)
                if not captcha_visible(page):
                    continue
                shot = screenshot_challenge(page, tag=f"r{round_i}b")
        except Exception:
            pass
        if not challenge_content_ready(shot):
            _log("challenge content not ready — waiting for assets and retrying")
            page.wait_for_timeout(1500)
            wait_for_challenge_canvas(page, timeout_s=10.0)
            shot = screenshot_challenge(page, tag=f"r{round_i}c")
            if not challenge_content_ready(shot):
                _log("still not ready — skip this round")
                page.wait_for_timeout(1500)
                continue
        use_backend = backend
        # If a previous drag/click didn't clear the same challenge, escalate
        if (
            round_i > 1
            and prev_instruction
            and backend in ("ocr", "auto", "hybrid")
        ):
            if os.getenv("TWOCAPTCHA_API_KEY") or os.getenv("TWO_CAPTCHA_API_KEY"):
                _log("prior round did not clear — escalating to 2Captcha Coordinates")
                use_backend = "twocaptcha"
            elif os.getenv("VISION_AGENT_FALLBACK", "1") not in ("0", "false", "False"):
                _log("prior round did not clear challenge — using agent backend")
                use_backend = "agent"
        plan = plan_for_screenshot(shot, backend=use_backend)
        prev_instruction = (plan.instruction or "")[:120]
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
            # keep trying later rounds (challenge may change) unless last round
            if round_i == max_rounds:
                return False
            page.wait_for_timeout(1500)
            continue
        applied_n = apply_clicks(page, plan, shot)
        page.wait_for_timeout(800)
        if applied_n <= 0:
            _log("no clicks/drags applied — skipping Verify to avoid burning attempt")
            page.wait_for_timeout(800)
            continue
        # Letter-grid + drag challenges need an explicit Next/Verify inside the widget
        # Only auto-advance when we actually performed actions
        needs_verify = bool(plan.drags) or (
            bool(plan.clicks)
            and "letter" in (plan.instruction or "").lower()
            and "not found" not in (plan.notes or "").lower()
        )
        if needs_verify or verify_button_visible(page):
            if click_challenge_next(page):
                page.wait_for_timeout(1500)
        page.wait_for_timeout(800)
        # Never click page-level Next/Verify — that hits Riot social OAuth buttons.
        shot2 = screenshot_challenge(page, tag=f"r{round_i}_after")
        _log(f"after-click shot {shot2.name}")
        # If Verify appeared after drag (Skip → Verify), click it now
        if verify_button_visible(page):
            _log("Verify visible after action — clicking")
            if click_challenge_next(page):
                page.wait_for_timeout(2000)
        try:
            if page_has_riot_oops(page) or page_has_invalid_captcha(page):
                _log("Riot error/invalid captcha after action — fail")
                return False
            if page_looks_social_oauth(page):
                _log(f"social OAuth misclick ({(page.url or '')[:80]}) — fail")
                return False
        except Exception:
            pass
        # If checkbox frame shows success, done
        try:
            if not captcha_visible(page):
                page.wait_for_timeout(2000)
                if page_has_riot_oops(page) or page_has_invalid_captcha(page):
                    _log("Oops/invalid after captcha cleared — fail")
                    return False
                if page_looks_social_oauth(page):
                    _log(f"social OAuth after captcha ({(page.url or '')[:80]}) — fail")
                    return False
                if page_looks_auth_progress(page):
                    _log("captcha gone — MFA/account success")
                    return True
                _log("captcha iframe gone but still on login — continue/finish")
                # One more wait in case MFA mounts slowly
                page.wait_for_timeout(2000)
                if page_looks_social_oauth(page):
                    return False
                if page_looks_auth_progress(page):
                    return True
                if page_has_riot_oops(page) or page_has_invalid_captcha(page):
                    return False
                return True
        except Exception:
            pass
    _log("max vision rounds exhausted")
    return False


def page_looks_social_oauth(page) -> bool:
    u = (page.url or "").lower()
    return any(
        h in u
        for h in (
            "facebook.com",
            "accounts.google",
            "apple.com",
            "live.com",
            "login.live.com",
            "xbox.com",
            "playstation.com",
            "my.account.sony.com",
            "sonyacct",
        )
    )


def page_looks_past_login(page) -> bool:
    u = page.url or ""
    return "account.riotgames.com" in u and "authenticate" not in u
