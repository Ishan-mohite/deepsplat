"""Floor alignment: find gravity direction and the floor plane of a splat, return a rigid transform
into a frame where the floor is z = 0, +z is up, units are metres (the splat is metric).

1. Gravity: dominant surface-normal direction (largest eigenvector of sum(area * n n^T)). Floors and
   ceilings of a building dominate it, so it also captures a tilted capture (phone not held level).
2. Sign: the floor is the big horizontal plane with (almost) nothing below it; the ceiling has
   nothing above it. The sign with fewer gaussians beyond its extreme plane wins.
3. Floor plane: disc-like gaussians with near-vertical normals, lowest dense height peak, RANSAC +
   SVD refinement -> exact normal and offset (removes residual tilt of a few degrees).
4. Optional yaw: if walls are rectilinear, rotate so they are parallel to x/y.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .source import normals


@dataclass
class Alignment:
    T: np.ndarray                 # 4x4 splat -> aligned (z up, floor at 0)
    up_in_splat: np.ndarray       # unit vector (splat frame) pointing up
    floor_normal_in_splat: np.ndarray
    floor_offset: float           # floor plane: n . p = offset in the splat frame
    tilt_deg: float               # angle between gravity estimate and fitted floor normal
    yaw_deg: float
    stats: dict

    def apply(self, p: np.ndarray) -> np.ndarray:
        return p @ self.T[:3, :3].T + self.T[:3, 3]


def _rot_to_z(n: np.ndarray) -> np.ndarray:
    z = np.array([0, 0, 1.0])
    v = np.cross(n, z)
    c = float(n @ z)
    if np.linalg.norm(v) < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1, -1, -1.0])
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def estimate(xyz: np.ndarray, scale: np.ndarray, quat: np.ndarray, yaw: str = "auto", seed: int = 0) -> Alignment:
    rng = np.random.default_rng(seed)
    nrm, flat = normals(scale, quat)
    s = np.sort(scale, 1)
    area = s[:, 2] * s[:, 1]
    disc = flat < 0.5
    w = area * disc
    # 1 gravity: the direction in which horizontal surfaces are sharpest. Normal statistics alone are
    #   misleading in round rooms (curved walls spread normals over all horizontal directions), but a
    #   floor is a razor-sharp peak of height values, so score candidates by their tallest 3 cm bin.
    def sharp(d):
        al = disc & (np.abs(nrm @ d) > 0.95)
        if al.sum() < 100:
            return 0
        hh = xyz[al] @ d
        return int(np.histogram(hh, bins=np.arange(hh.min(), hh.max() + 0.03, 0.03))[0].max())

    M = (nrm * w[:, None]).T @ nrm
    ev, evec = np.linalg.eigh(M)
    share = float(ev[-1] / ev.sum())
    cands = [np.eye(3)[i] for i in range(3)] + [evec[:, i] for i in range(3)]
    g = max(cands, key=sharp)
    best_s = sharp(g)
    for step in (2.0, 1.0, 0.5, 0.25):                        # local search over small tilts (degrees)
        improved = True
        while improved:
            improved = False
            a1 = np.cross(g, [1, 0, 0] if abs(g[0]) < 0.9 else [0, 1, 0])
            a1 /= np.linalg.norm(a1)
            a2 = np.cross(g, a1)
            for da, db in ((step, 0), (-step, 0), (0, step), (0, -step)):
                d = g + np.tan(np.radians(da)) * a1 + np.tan(np.radians(db)) * a2
                d /= np.linalg.norm(d)
                sc_ = sharp(d)
                if sc_ > best_s:
                    g, best_s, improved = d, sc_, True

    # 2 sign
    def beyond(up):
        h = xyz @ up
        sel = (np.abs(nrm @ up) > 0.9) & disc
        hist, edges = np.histogram(h[sel], bins=np.arange(h[sel].min(), h[sel].max() + 0.05, 0.05))
        big = np.flatnonzero(hist >= 0.25 * hist.max())
        lo = edges[big[0]]
        return float((h < lo - 0.1).mean()), lo
    f_pos, lo_pos = beyond(g)
    f_neg, lo_neg = beyond(-g)
    up = g if f_pos <= f_neg else -g

    # 3 floor plane
    h = xyz @ up
    cand = (np.abs(nrm @ up) > 0.9) & disc
    hc = h[cand]
    hist, edges = np.histogram(hc, bins=np.arange(hc.min(), hc.max() + 0.03, 0.03))
    big = np.flatnonzero(hist >= 0.25 * hist.max())
    peak = (edges[big[0]] + edges[big[0] + 1]) / 2
    pts = xyz[cand & (np.abs(h - peak) < 0.12)]
    if len(pts) > 200_000:
        pts = pts[rng.choice(len(pts), 200_000, replace=False)]
    best = None
    for _ in range(500):
        a = pts[rng.choice(len(pts), 3, replace=False)]
        n = np.cross(a[1] - a[0], a[2] - a[0])
        L = np.linalg.norm(n)
        if L < 1e-9:
            continue
        n /= L
        n = n if n @ up > 0 else -n
        if n @ up < 0.97:
            continue
        inl = (np.abs((pts - a[0]) @ n) < 0.02).sum()
        if best is None or inl > best[0]:
            best = (inl, n, a[0])
    if best is None:
        n0, c0 = up, up * peak
    else:
        _, n0, p0 = best
        sel = pts[np.abs((pts - p0) @ n0) < 0.03]
        c0 = sel.mean(0)
        n0 = np.linalg.svd(sel - c0, full_matrices=False)[2][2]
        n0 = n0 if n0 @ up > 0 else -n0
    offset = float(n0 @ c0)
    tilt = float(np.degrees(np.arccos(np.clip(n0 @ up, -1, 1))))

    R = _rot_to_z(n0)
    # 4 yaw
    psi = 0.0
    hor = (np.abs(nrm @ n0) < 0.2) & disc
    ang_strength = 0.0
    if hor.any():
        nh = nrm[hor] @ R.T
        th = np.arctan2(nh[:, 1], nh[:, 0])
        z4 = (w[hor] * np.exp(4j * th)).sum() / w[hor].sum()
        ang_strength = float(abs(z4))
        if yaw == "auto" and ang_strength > 0.3 or yaw == "force":
            psi = -np.angle(z4) / 4
    c, s_ = np.cos(psi), np.sin(psi)
    Rz = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])
    R = Rz @ R
    # origin: floor at z=0; xy origin at the floor's centroid (rounded to 0.5 m for stable maps)
    flo = (xyz[cand & (np.abs(h - peak) < 0.1)] @ R.T)
    xy0 = np.round(np.median(flo[:, :2], axis=0) * 2) / 2
    t = -R @ (n0 * offset)           # moves a floor point to z=0 ...
    t = np.array([-xy0[0], -xy0[1], 0.0]) + (t - (R @ t) * 0)  # keep z shift only
    # exact: z' = (R p)_z + tz with (R p)_z = n0 . p  (R rows: third row is n0)
    tz = -offset
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = [-xy0[0], -xy0[1], tz]
    stats = {"gravity_share": round(share, 3), "beyond_pos": round(f_pos, 4), "beyond_neg": round(f_neg, 4),
             "floor_inliers": int(best[0]) if best else 0, "floor_candidates": int(len(pts)),
             "yaw_strength": round(ang_strength, 3), "floor_peak_height": round(float(peak), 3)}
    return Alignment(T, up, n0, offset, tilt, float(np.degrees(psi)), stats)
