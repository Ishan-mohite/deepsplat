"""Pick video frames for multi-frame localization (as MultiSet does: 4-6 frames ~500 ms apart,
spread over at least half a metre of camera motion)."""

import numpy as np

from .cameras import Frame


def select_frames(frames: list[Frame], n: int = 6, min_dt: float = 0.5, min_spread_m: float = 0.5) -> list[Frame]:
    good = [f for f in frames if f.tracking_state == "normal" and f.c2w is not None and f.t is not None]
    good.sort(key=lambda f: f.t)
    if not good:
        return []
    # thin out to >= min_dt apart, then take n evenly spread over the capture
    thinned = [good[0]]
    for f in good[1:]:
        if f.t - thinned[-1].t >= min_dt:
            thinned.append(f)
    if len(thinned) > n:
        idx = np.linspace(0, len(thinned) - 1, n).round().astype(int)
        thinned = [thinned[i] for i in idx]
    return thinned


def spread_m(frames: list[Frame]) -> float:
    if len(frames) < 2:
        return 0.0
    c = np.array([f.c2w[:3, 3] for f in frames])
    return float(np.linalg.norm(c.max(axis=0) - c.min(axis=0)))
