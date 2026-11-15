"""2.5D elevation + traversability layers from floor-aligned gaussians (z up, floor = 0, metres).

Layers (all share one grid, row 0 = max y so it displays like a top-down photo):

* ``dsm``       highest opaque surface per cell (what a drone / overhead sensor sees)
* ``ground``    supporting surface per cell: lowest dense cluster of up-facing gaussians
* ``obstacle``  height of the tallest thing inside the robot's body band above ``ground`` (0 = free)
* ``slope``     ground slope in degrees, ``step`` largest ground step to a neighbour in metres
* ``cost``      0 free ... 100 lethal, 255 unknown (nav2 / grid_map style), inflated by robot radius
* ``trav``      boolean: a ground robot may stand here
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi


@dataclass
class Config:
    res: float = 0.05               # m per cell
    robot_height: float = 0.7       # body band above ground that must be clear
    robot_radius: float = 0.25      # inflation (m)
    clearance_min: float = 0.05     # ignore floor texture below this height above ground
    max_slope_deg: float = 20.0
    max_step_m: float = 0.15
    min_pts_ground: int = 2
    min_pts_obstacle: int = 2
    fill_holes_m: float = 0.30      # interpolate ground gaps up to this size
    z_max: float = 2.4              # ignore everything above this height above the floor (ceilings, lights)
    margin_m: float = 0.5


def _cells(xy, lo, res, shape):
    ix = np.floor((xy[:, 0] - lo[0]) / res).astype(np.int64)
    iy = np.floor((xy[:, 1] - lo[1]) / res).astype(np.int64)
    ok = (ix >= 0) & (ix < shape[1]) & (iy >= 0) & (iy < shape[0])
    return ix, iy, ok


def build(p: np.ndarray, n: np.ndarray, rgb: np.ndarray, cfg: Config = Config()) -> dict:
    """p: (N,3) aligned positions, n: (N,3) aligned normals, rgb: (N,3) 0..1."""
    keep = (p[:, 2] > -0.3) & (p[:, 2] < cfg.z_max)
    p, n, rgb = p[keep], n[keep], rgb[keep]
    lo = p[:, :2].min(0) - cfg.margin_m
    hi = p[:, :2].max(0) + cfg.margin_m
    W = int(np.ceil((hi[0] - lo[0]) / cfg.res))
    H = int(np.ceil((hi[1] - lo[1]) / cfg.res))
    shape = (H, W)
    ix, iy, ok = _cells(p[:, :2], lo, cfg.res, (H, W))
    row = (H - 1 - iy)                                      # image row: y up
    flat = np.where(ok, row * W + ix, -1)

    # --- dsm
    dsm = np.full(H * W, np.nan)
    m = flat >= 0
    tmp = np.full(H * W, -np.inf)
    np.maximum.at(tmp, flat[m], p[m, 2])
    dsm[np.isfinite(tmp)] = tmp[np.isfinite(tmp)]
    dsm = dsm.reshape(shape)

    # --- ground: lowest cluster of up-facing gaussians (robust to floaters below the floor)
    up = m & (np.abs(n[:, 2]) > 0.7)                      # horizontal surface (normal sign is arbitrary in a splat)
    fi, fz = flat[up], p[up, 2]
    order = np.lexsort((fz, fi))
    fi, fz = fi[order], fz[order]
    starts = np.r_[0, np.flatnonzero(fi[1:] != fi[:-1]) + 1]
    counts = np.diff(np.r_[starts, len(fi)])
    ground = np.full(H * W, np.nan)
    gcount = np.zeros(H * W, np.int32)
    sel = counts >= cfg.min_pts_ground                      # k-th lowest point per cell: ignores 1-2 floaters
    ground[fi[starts][sel]] = fz[starts[sel] + (cfg.min_pts_ground - 1)]
    gcount[fi[starts]] = counts
    ground = ground.reshape(shape)
    gcount = gcount.reshape(shape)

    # --- fill small ground holes (nearest known ground within fill_holes_m)
    known = ~np.isnan(ground)
    dist, (ri, ci) = ndi.distance_transform_edt(~known, return_indices=True)
    fillable = ~known & (dist * cfg.res <= cfg.fill_holes_m)
    ground_f = ground.copy()
    ground_f[fillable] = ground[ri[fillable], ci[fillable]]
    ground_f = np.where(np.isnan(ground_f), np.nan, ndi.median_filter(np.nan_to_num(ground_f), size=3))
    has_ground = ~np.isnan(ground_f)

    # --- obstacles inside the robot band above ground
    g_at = ground_f.reshape(-1)[np.where(flat >= 0, flat, 0)]
    hgt = p[:, 2] - g_at
    band = m & ~np.isnan(g_at) & (hgt > cfg.clearance_min) & (hgt < cfg.robot_height)
    obst = np.zeros(H * W)
    cnt = np.zeros(H * W, np.int32)
    np.maximum.at(obst, flat[band], hgt[band])
    np.add.at(cnt, flat[band], 1)
    obst[cnt < cfg.min_pts_obstacle] = 0
    obstacle = obst.reshape(shape)
    occupied = obstacle > 0
    occupied = ndi.binary_closing(occupied, iterations=1) | occupied

    # --- slope / step
    g = np.where(has_ground, ground_f, np.nan)
    gs = ndi.uniform_filter(np.nan_to_num(g), 5) / np.maximum(ndi.uniform_filter(has_ground.astype(float), 5), 1e-6)
    gy, gx = np.gradient(np.where(has_ground, gs, 0.0), cfg.res)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    slope[~has_ground] = np.nan
    gmax = ndi.maximum_filter(np.where(has_ground, g, -9), size=3)
    gmin = ndi.minimum_filter(np.where(has_ground, g, 9), size=3)
    step = np.where(has_ground, gmax - gmin, np.nan)
    step[(gmax < -5) | (gmin > 5)] = np.nan

    bad = occupied | (np.nan_to_num(slope) > cfg.max_slope_deg) | (np.nan_to_num(step) > cfg.max_step_m)
    free = has_ground & ~bad
    # inflate non-free (known) cells by robot radius for the planning mask
    infl = ndi.distance_transform_edt(~(bad & has_ground | (~has_ground)))
    trav = free & (infl * cfg.res >= cfg.robot_radius)

    cost = np.full(shape, 255, np.uint8)                    # unknown
    cost[has_ground] = 0
    soft = has_ground & (infl * cfg.res < cfg.robot_radius * 2)
    cost[soft] = np.clip(100 * (1 - infl[soft] * cfg.res / (cfg.robot_radius * 2)), 1, 99).astype(np.uint8)
    cost[bad & has_ground] = 100

    # --- colour top-down (highest gaussian wins)
    order = np.argsort(p[:, 2])
    img = np.full((H * W, 3), 0.96)
    img[flat[order][flat[order] >= 0]] = rgb[order][flat[order] >= 0]
    img = img.reshape(H, W, 3)
    seen = np.zeros(H * W, bool)
    seen[flat[m]] = True
    seen = seen.reshape(shape)
    for _ in range(2):
        for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            src = np.roll(np.roll(seen, dy, 0), dx, 1)
            take = ~seen & src
            img[take] = np.roll(np.roll(img, dy, 0), dx, 1)[take]
            seen |= take

    return {"dsm": dsm, "ground": ground_f, "ground_raw": ground, "obstacle": obstacle, "slope": slope,
            "step": step, "cost": cost, "trav": trav, "occupied": occupied, "has_ground": has_ground,
            "ground_count": gcount, "topdown": img, "origin_xy": lo, "res": cfg.res,
            "shape": shape, "x_range": (lo[0], lo[0] + W * cfg.res), "y_range": (lo[1], lo[1] + H * cfg.res)}
