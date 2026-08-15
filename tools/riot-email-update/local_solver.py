"""
Local in-bot hCaptcha planner — training / eval path.

Honest scope
------------
A general local model that solves *all* hCaptcha Enterprise types is a large
research effort (rotating categories, drag, letter grids, session bind).

What we *can* do now:
  1. Use 2Captcha CoordinatesTask as an oracle while solving in-browser
     (see twocaptcha_click.py) — tokens mint in-session, Riot bind can hold.
  2. Save labeled samples under debug/training_samples/{ts}/
       challenge.png + label.json  (clicks / drags + instruction)
  3. Prefer local OpenCV heuristics (vision_captcha.plan_ocr) for recurring
     types: letter grids + drag-astronaut.
  4. Eval local CV against oracle labels with this module; improve thresholds
     until local hits more often and 2cap is only the fallback.

Usage
-----
  python local_solver.py eval          # score local CV vs saved labels
  python local_solver.py list          # show collected samples
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent / "debug" / "training_samples"


def list_samples() -> list[Path]:
    if not SAMPLES_DIR.is_dir():
        return []
    return sorted(p for p in SAMPLES_DIR.iterdir() if (p / "label.json").is_file())


def _center(drag_or_click: dict) -> tuple[float, float]:
    if "x1" in drag_or_click:
        return (
            (drag_or_click["x1"] + drag_or_click["x2"]) / 2,
            (drag_or_click["y1"] + drag_or_click["y2"]) / 2,
        )
    return float(drag_or_click["x"]), float(drag_or_click["y"])


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def eval_samples(tol_px: float = 55.0) -> dict:
    """Compare local CV plans to oracle labels. Returns summary stats."""
    from vision_captcha import plan_ocr

    samples = list_samples()
    results = []
    for folder in samples:
        label = json.loads((folder / "label.json").read_text())
        img = folder / (label.get("image") or "challenge.png")
        if not img.is_file():
            continue
        plan = plan_ocr(img)
        oracle_drags = label.get("drags") or []
        oracle_clicks = label.get("clicks") or []
        hit = False
        detail = ""
        if oracle_drags and plan.drags:
            o = oracle_drags[0]
            p = plan.drags[0]
            d_src = _dist((o["x1"], o["y1"]), (p.x1, p.y1))
            d_dst = _dist((o["x2"], o["y2"]), (p.x2, p.y2))
            hit = d_src <= tol_px and d_dst <= tol_px
            detail = f"drag src_err={d_src:.0f} dst_err={d_dst:.0f}"
        elif oracle_clicks and plan.clicks:
            # Match each oracle click to nearest local click within tol
            matched = 0
            for oc in oracle_clicks:
                ocxy = (oc["x"], oc["y"])
                if any(_dist(ocxy, (c.x, c.y)) <= tol_px for c in plan.clicks):
                    matched += 1
            hit = matched >= max(1, len(oracle_clicks) // 2)
            detail = f"clicks matched {matched}/{len(oracle_clicks)}"
        else:
            detail = (
                f"local clicks={len(plan.clicks)} drags={len(plan.drags or [])} "
                f"oracle clicks={len(oracle_clicks)} drags={len(oracle_drags)}"
            )
        results.append(
            {
                "id": folder.name,
                "instruction": (label.get("instruction") or "")[:60],
                "hit": hit,
                "detail": detail,
                "local_backend": plan.backend,
            }
        )
        print(f"[{'HIT' if hit else 'miss'}] {folder.name} {detail} | {results[-1]['instruction']}")

    n = len(results)
    hits = sum(1 for r in results if r["hit"])
    summary = {"samples": n, "hits": hits, "hit_rate": (hits / n) if n else 0.0, "results": results}
    print(f"\nlocal CV hit_rate={summary['hit_rate']:.0%} ({hits}/{n}) tol={tol_px}px")
    return summary


def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "list").lower()
    if cmd == "list":
        samples = list_samples()
        print(f"{len(samples)} samples in {SAMPLES_DIR}")
        for p in samples:
            lab = json.loads((p / "label.json").read_text())
            print(
                f"  {p.name}  clicks={len(lab.get('clicks') or [])} "
                f"drags={len(lab.get('drags') or [])}  "
                f"{(lab.get('instruction') or '')[:50]!r}"
            )
        return 0
    if cmd == "eval":
        eval_samples()
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
