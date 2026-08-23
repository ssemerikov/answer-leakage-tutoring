"""Emit the TikZ coordinates for the screen histogram from a screen file.

WHY. Figure `fig:screen` in the manuscript is a histogram of baseline
identifiability with the retention threshold marked. Its coordinates were
hand-transcribed from the Kiro screen. Hand-transcription is how the numbers in
this project drift: `corr = 0.95` outlived its data by 30 items, and the
"rejections at every level" claim survived two contradictory revisions.

If the primary screen changes -- and it may, since the Ollama panel retains far
fewer items (§4.9) -- the figure must be regenerated, not retyped. This prints
the exact `\\addplot` block and the annotation positions, computed from the file.

Usage:
    python3 make_screen_figure.py --screen data/runs/screen_bankA_ol.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import config as C
import solvability as S


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", required=True)
    ap.add_argument("--bins", type=int, default=10)
    args = ap.parse_args()

    p = Path(args.screen)
    if not p.is_absolute():
        p = C.ROOT / p
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    if not rows:
        raise SystemExit(f"no records in {p}")

    thr = (1.0 / 3.0) + C.HEADROOM_MARGIN
    kept = [r["baseline"] for r in rows if r["passes_headroom"]]
    rej = [r["baseline"] for r in rows if not r["passes_headroom"]]

    counts = [0] * args.bins
    for r in rows:
        b = min(int(r["baseline"] * args.bins), args.bins - 1)
        counts[b] += 1

    width = 1.0 / args.bins
    coords = " ".join(f"({i * width:.1f},{c})" for i, c in enumerate(counts))
    ymax = max(counts) + 1

    print(f"% generated from {p.name}  n={len(rows)}  "
          f"retained={len(kept)}  discarded={len(rej)}")
    print(f"% threshold {thr:.3f};  retained [{min(kept):.2f}, {max(kept):.2f}]"
          if kept else "% no retained items")
    if rej:
        print(f"% discarded [{min(rej):.2f}, {max(rej):.2f}]")
    if kept and rej:
        gap = min(rej) - max(kept)
        # Same comparison as the retention rule (solvability.at_or_below), so
        # the figure cannot disagree with the screen it depicts.
        inside = S.at_or_below(max(kept), thr) and S.at_or_below(thr, min(rej))
        strict = max(kept) < thr < min(rej)
        print(f"% invariance band [{max(kept):.2f}, {min(rej):.2f}] width {gap:.2f}; "
              f"threshold inside: {inside}")
        if not inside:
            print("% WARNING: the threshold is NOT inside the empty band. The "
                  "manuscript's invariance claim does not hold for this screen.")
        elif not strict:
            print("% WARNING: the threshold sits EXACTLY ON an item's baseline. "
                  "The partition is decided by the tie rule, not by an empty "
                  "band, so the manuscript must not claim that any threshold in "
                  "the band gives the same partition.")
    print()
    print(f"  ymin=0, ymax={ymax},")
    print(f"\\addplot[fill=gray!35, draw=gray!60] coordinates {{")
    print(f"  {coords} ({args.bins * width:.1f},0)}};")
    print(f"\\draw[dashed, thick] (axis cs:{thr:.3f},0) -- (axis cs:{thr:.3f},{ymax});")
    print(f"\\node[font=\\scriptsize, anchor=south west] at "
          f"(axis cs:{thr + 0.02:.2f},{ymax * 0.73:.1f}) {{threshold ${thr:.3f}$}};")
    print(f"\\node[font=\\scriptsize, anchor=south] at (axis cs:0.25,{ymax * 0.78:.1f}) "
          f"{{retained ({len(kept)})}};")
    print(f"\\node[font=\\scriptsize, anchor=south] at (axis cs:0.80,{ymax * 0.47:.1f}) "
          f"{{discarded ({len(rej)})}};")


if __name__ == "__main__":
    main()
