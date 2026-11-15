"""Pick video frames for multi-frame localization: tracking normal, >= 0.5 s apart, spread over the capture.

MultiSet's SDK sends 4-6 frames. On real phone captures of the auditorium that was too few: only some
frames localize and a few localize to a wrong place, so a group of >= 3 mutually consistent frames needs
more candidates. Sweep over 15 uploaded captures (6 vs 12 frames): marginal captures went 0.17 -> 0.50 and
0.17 -> 0.44 confidence, the ones that already worked stayed the same; most captures only contain 9-10 frames
at 0.5 s spacing, so going beyond ~12 changes nothing. Cost: ~0.3 s per extra frame."""

import numpy as np

from .cameras import Frame


def select_frames(frames: list[Frame], n: int = 12, min_dt: float = 0.5, min_spread_m: float = 0.5) -> list[Frame]:
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
