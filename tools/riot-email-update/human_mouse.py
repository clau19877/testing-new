"""
Human-like mouse trajectories for in-browser hCaptcha actions.

Multibot-style solvers replay curved cursor paths instead of teleporting
the pointer. This module generates cubic-Bezier paths with jitter and
variable timing, then replays them through Playwright/Patchright mouse APIs.
"""

from __future__ import annotations

import math
import os
import random
import time
from typing import Iterable


def human_mouse_enabled() -> bool:
    return os.getenv("HUMAN_MOUSE", "1") not in ("0", "false", "False")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def bezier_points(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    steps: int | None = None,
) -> list[tuple[float, float]]:
    """Cubic Bezier from (x0,y0) → (x1,y1) with randomized control points."""
    dist = math.hypot(x1 - x0, y1 - y0)
    if steps is None:
        steps = max(12, min(48, int(dist / 12) + random.randint(8, 18)))

    # Control points offset perpendicular to the travel vector
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    dx, dy = x1 - x0, y1 - y0
    # Perpendicular unit
    length = dist or 1.0
    px, py = -dy / length, dx / length
    spread = _clamp(dist * random.uniform(0.15, 0.45), 20.0, 180.0)
    c1x = x0 + dx * random.uniform(0.2, 0.45) + px * random.uniform(-spread, spread)
    c1y = y0 + dy * random.uniform(0.2, 0.45) + py * random.uniform(-spread, spread)
    c2x = x0 + dx * random.uniform(0.55, 0.85) + px * random.uniform(-spread, spread)
    c2y = y0 + dy * random.uniform(0.55, 0.85) + py * random.uniform(-spread, spread)

    # Occasional slight overshoot then settle
    overshoot = random.random() < 0.35 and dist > 80
    tx, ty = x1, y1
    if overshoot:
        ox = random.uniform(4, 14) * (1 if dx >= 0 else -1)
        oy = random.uniform(4, 14) * (1 if dy >= 0 else -1)
        tx, ty = x1 + ox, y1 + oy

    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        # Ease-in-out
        te = t * t * (3 - 2 * t)
        u = 1 - te
        x = (
            u**3 * x0
            + 3 * u**2 * te * c1x
            + 3 * u * te**2 * c2x
            + te**3 * tx
        )
        y = (
            u**3 * y0
            + 3 * u**2 * te * c1y
            + 3 * u * te**2 * c2y
            + te**3 * ty
        )
        # Micro jitter (smaller near the end)
        jitter = (1 - te) * random.uniform(0.0, 1.2)
        x += random.uniform(-jitter, jitter)
        y += random.uniform(-jitter, jitter)
        pts.append((x, y))

    if overshoot:
        # Settle back to true target
        for j in range(1, 6):
            t = j / 5
            pts.append((tx + (x1 - tx) * t, ty + (y1 - ty) * t))
    else:
        pts[-1] = (x1, y1)
    return pts


def _step_delay_ms(dist_remaining: float, total: float) -> float:
    """Faster mid-stroke, slower near start/end (Fitts-ish)."""
    progress = 1.0 - (dist_remaining / total) if total else 1.0
    base = random.uniform(8, 18)
    if progress < 0.15 or progress > 0.85:
        base *= random.uniform(1.4, 2.2)
    else:
        base *= random.uniform(0.6, 1.0)
    return base


def move_human(page, x: float, y: float, *, from_xy: tuple[float, float] | None = None) -> None:
    """Move mouse along a human-like curve to (x, y)."""
    if from_xy is None:
        try:
            pos = page.evaluate(
                "() => ({x: window.__hm_x || 0, y: window.__hm_y || 0})"
            )
            from_xy = (float(pos.get("x") or 0), float(pos.get("y") or 0))
            if from_xy == (0.0, 0.0):
                from_xy = (
                    random.uniform(200, 600),
                    random.uniform(200, 500),
                )
        except Exception:
            from_xy = (random.uniform(200, 600), random.uniform(200, 500))

    pts = bezier_points(from_xy[0], from_xy[1], x, y)
    total = math.hypot(x - from_xy[0], y - from_xy[1]) or 1.0
    for i, (px, py) in enumerate(pts):
        page.mouse.move(px, py)
        rem = math.hypot(x - px, y - py)
        delay = _step_delay_ms(rem, total)
        page.wait_for_timeout(delay)
    try:
        page.evaluate(
            "([x, y]) => { window.__hm_x = x; window.__hm_y = y; }",
            [x, y],
        )
    except Exception:
        pass


def click_human(page, x: float, y: float, *, button: str = "left") -> None:
    """Curve to point, pause, then press/release with human timing."""
    move_human(page, x, y)
    page.wait_for_timeout(random.uniform(40, 140))
    page.mouse.down(button=button)
    page.wait_for_timeout(random.uniform(35, 110))
    page.mouse.up(button=button)
    page.wait_for_timeout(random.uniform(80, 220))


def drag_human(
    page,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> None:
    """Curve to source, press, curve-drag to destination, release."""
    move_human(page, x1, y1)
    page.wait_for_timeout(random.uniform(80, 200))
    page.mouse.down()
    page.wait_for_timeout(random.uniform(60, 160))

    pts = bezier_points(x1, y1, x2, y2, steps=max(20, int(math.hypot(x2 - x1, y2 - y1) / 8)))
    total = math.hypot(x2 - x1, y2 - y1) or 1.0
    for px, py in pts[1:]:
        page.mouse.move(px, py)
        rem = math.hypot(x2 - px, y2 - py)
        page.wait_for_timeout(_step_delay_ms(rem, total) * 1.15)

    page.wait_for_timeout(random.uniform(80, 180))
    page.mouse.up()
    page.wait_for_timeout(random.uniform(120, 280))
    try:
        page.evaluate(
            "([x, y]) => { window.__hm_x = x; window.__hm_y = y; }",
            [x2, y2],
        )
    except Exception:
        pass


def replay_path(
    page,
    path: Iterable[tuple[float, float, float]],
) -> None:
    """
    Replay an external trajectory: iterable of (x, y, delay_ms).
    Used when a Multibot-style API returns an exact cursor path.
    """
    for x, y, delay_ms in path:
        page.mouse.move(float(x), float(y))
        if delay_ms and delay_ms > 0:
            page.wait_for_timeout(float(delay_ms))
        try:
            page.evaluate(
                "([x, y]) => { window.__hm_x = x; window.__hm_y = y; }",
                [float(x), float(y)],
            )
        except Exception:
            pass
