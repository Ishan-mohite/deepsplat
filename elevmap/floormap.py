"""Floor map: a clean top-down plan of the space, built from the same layers as the elevation map.

Where ``floor_plan.png`` (inspection/floorplan.py) is a photo-like render of the splat from above, the
floor map shows what the space is made of, in the deepsplat design tokens:

* floor                     ``--card``    the ground the site stands on
* raised levels             ``--paper`` -> ``--line-2``, darker the higher (steps, tiers, platforms)
* level edges               ``--muted``   where the ground jumps by more than ``edge_step_m``
* furniture / low objects   ``--line-2`` -> ``--muted``, darker the taller (tables, seats, machines)
* walls / full-height       ``--ink-2``   anything at least ``wall_min_m`` above its ground
* unknown                   transparent

Input: z-up points with the floor at z = 0 (metres) and, optionally, their surface normals. Without
normals (plain point clouds, isotropic gaussians) every point may support the ground.

``meta`` uses the floor-plan convention of the backend, x / y unchanged from the input:
    px = (x - origin_x) / m_per_px,  py = (origin_y - y) / m_per_px
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from . import grid

# design tokens (design/system/tokens.css)
CARD, PAPER, PAPER_2, LINE_2, MUTED, INK_2 = ((251, 250, 247), (241, 238, 230), (232, 226, 212),
                                              (211, 203, 187), (125, 120, 106), (74, 81, 99))


def layers(p: np.ndarray, n: np.ndarray | None, *, res: float = 0.05, z_max: float = 2.2,
           wall_min_m: float = 1.3, raised_min_m: float = 0.12, edge_step_m: float = 0.12,
           min_blob_m2: float = 0.15) -> dict:
    """Classify the grid cells. Returns the elevation grid (see grid.build) plus
    ``region`` / ``raised`` / ``furniture`` / ``wall`` / ``edge`` masks and ``height`` (ground, m)."""
    if n is None:
        n = np.tile([0.0, 0.0, 1.0], (len(p), 1))
    # obstacle band = everything up to z_max above the local ground, so walls and furniture separate by height
    cfg = grid.Config(res=res, robot_height=z_max, robot_radius=0.0, z_max=z_max, margin_m=0.3)
    g = grid.build(p, n, np.full((len(p), 3), 0.5), cfg)
    H, W = g["shape"]

    # tall things above cells without ground under them (wall faces, shelves): count points per cell
    ix = np.floor((p[:, 0] - g["origin_xy"][0]) / res).astype(np.int64)
    iy = np.floor((p[:, 1] - g["origin_xy"][1]) / res).astype(np.int64)
    ok = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H) & (p[:, 2] > wall_min_m * 0.6) & (p[:, 2] < z_max)
    tall = np.zeros(H * W, np.int32)
    np.add.at(tall, (H - 1 - iy[ok]) * W + ix[ok], 1)

    return classify(g, tall.reshape(H, W) >= 3, wall_min_m=wall_min_m, raised_min_m=raised_min_m,
                    edge_step_m=edge_step_m, min_blob_m2=min_blob_m2)


def _drop_small(mask: np.ndarray, min_cells: float) -> np.ndarray:
    lab, k = ndi.label(mask)
    if not k:
        return mask
    size = ndi.sum(mask, lab, np.arange(1, k + 1))
    return np.isin(lab, 1 + np.flatnonzero(size >= min_cells))


def classify(g: dict, tall: np.ndarray | None = None, *, wall_min_m: float = 1.3, raised_min_m: float = 0.12,
             edge_step_m: float = 0.12, min_blob_m2: float = 0.15, max_hole_m2: float = 1.0) -> dict:
    """Masks of the floor map from the grid layers (ground, obstacle, step, dsm). `tall`: cells with points
    high above the floor (walls seen without ground under them)."""
    cell = g["res"] ** 2
    # grid.build fills ground gaps up to 30 cm, also outwards past the walls: keep cells near real points only
    seen = ~np.isnan(g["dsm"])
    has = g["has_ground"] & (ndi.binary_closing(seen, iterations=3, border_value=0) | seen)
    obst = np.where(has, g["obstacle"], 0.0)
    wall = (obst >= wall_min_m) | (~has & (tall if tall is not None else False))
    wall = _drop_small(wall, 0.02 / cell)
    # furniture: single noisy cells out, then shapes closed so a table reads as one piece
    furniture = (obst > 0) & ~wall
    furniture = ndi.binary_closing(_drop_small(ndi.binary_opening(furniture), 0.04 / cell), iterations=1) & has & ~wall
    height = np.where(has, g["ground"], np.nan)
    raised = has & (np.nan_to_num(height) > raised_min_m) & ~wall & ~furniture
    # level edges (tiers, steps), not the ground jitter around furniture
    edge = has & (np.nan_to_num(g["step"]) > edge_step_m) & ~wall & ~ndi.binary_dilation(furniture, iterations=2)
    edge = _drop_small(edge, 0.03 / cell)

    # drop floaters (islands smaller than min_blob_m2 or 1 % of the largest piece: they would only widen the
    # map's frame), fill small holes (unseen spots under chairs, reflections)
    region = has | wall
    lab, k = ndi.label(region)
    if k:
        size = ndi.sum(region, lab, np.arange(1, k + 1))
        region = np.isin(lab, 1 + np.flatnonzero(size >= max(min_blob_m2 / cell, 0.01 * size.max())))
    holes = ndi.binary_fill_holes(region) & ~region
    filled = holes & ~_drop_small(holes, max_hole_m2 / cell)
    region |= filled
    if filled.any():            # a filled hole takes the height of the ground next to it
        _, (ri, ci) = ndi.distance_transform_edt(~has, return_indices=True)
        height = np.where(filled, height[ri, ci], height)
        raised |= filled & (np.nan_to_num(height) > raised_min_m)
    for m in (wall, furniture, raised, edge):
        m &= region
    g.update(region=region, wall=wall, furniture=furniture, raised=raised, edge=edge, height=height,
             obstacle_h=obst)
    return g


def _smooth(mask: np.ndarray, k: int, sigma: float = 0.6) -> np.ndarray:
    """Upsample a cell mask by k with rounded, not stair-stepped, outlines."""
    return ndi.zoom(ndi.gaussian_filter(mask.astype(np.float32), sigma), k, order=1) > 0.5


def _lerp(a, b, t):
    t = np.clip(t, 0, 1)[..., None]
    return np.asarray(a, np.float32) * (1 - t) + np.asarray(b, np.float32) * t


def _crop(g: dict, pad: int) -> dict:
    """The masks cut to the mapped area (+ pad cells), so the image has no empty frame."""
    rows, cols = np.flatnonzero(g["region"].any(1)), np.flatnonzero(g["region"].any(0))
    if not len(rows):
        return g
    H, W = g["shape"]
    r0, r1 = max(rows[0] - pad, 0), min(rows[-1] + 1 + pad, H)
    c0, c1 = max(cols[0] - pad, 0), min(cols[-1] + 1 + pad, W)
    out = {k: g[k][r0:r1, c0:c1] for k in ("region", "raised", "height", "edge", "furniture", "obstacle_h", "wall")}
    ox, oy = (float(v) for v in g["origin_xy"])
    # origin_xy is the lower-left corner: rows below r1 are cut off, columns left of c0
    return {**out, "shape": (int(r1 - r0), int(c1 - c0)), "res": float(g["res"]),
            "origin_xy": (ox + int(c0) * g["res"], oy + int(H - r1) * g["res"])}


def render(g: dict, *, px_per_cell: int = 3, supersample: int = 2, pad_cells: int = 3) -> tuple[bytes, dict]:
    """PNG (RGBA, transparent where nothing was seen) + meta for the layers of `layers()`."""
    g = _crop(g, pad_cells)
    k = px_per_cell * supersample
    H, W = g["shape"]
    region = _smooth(g["region"], k)
    img = np.zeros(region.shape + (4,), np.float32)
    img[region] = CARD + (255,)

    # raised ground, tinted by height (continuous, so tiers read as terraces)
    hmax = max(float(np.nanmax(np.where(g["raised"], g["height"], np.nan), initial=0.0)), 0.5)
    h = np.where(g["raised"], np.nan_to_num(g["height"]), 0.0)
    h_up = ndi.zoom(ndi.grey_dilation(h, size=3), k, order=1)
    rs = _smooth(g["raised"], k) & region
    img[rs, :3] = _lerp(PAPER, LINE_2, h_up[rs] / hmax)

    ed = _smooth(g["edge"], k, sigma=0.4) & region
    img[ed, :3] = _lerp(img[ed, :3], MUTED, 0.35)

    # furniture, darker the taller; walls on top
    oh = ndi.zoom(ndi.grey_dilation(np.where(g["furniture"], g["obstacle_h"], 0.0), size=3), k, order=1)
    fu = _smooth(g["furniture"], k) & region
    img[fu, :3] = _lerp(LINE_2, MUTED, (oh[fu] - 0.3) / 1.0)
    wl = _smooth(ndi.binary_dilation(g["wall"]), k, sigma=0.5)        # a splat wall is one surface: draw it 3 cells thick
    img[wl] = INK_2 + (255,)

    # thin outline around the mapped area
    outline = region & ~ndi.binary_erosion(region, iterations=supersample + 1)
    img[outline & ~wl] = LINE_2 + (255,)

    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGBA")
    if supersample > 1:
        im = im.resize((W * px_per_cell, H * px_per_cell), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    res = g["res"]
    ox, oy = (float(v) for v in g["origin_xy"])
    meta = {"m_per_px": round(res / px_per_cell, 6), "origin_x": round(ox, 4), "origin_y": round(oy + H * res, 4),
            "width": W * px_per_cell, "height": H * px_per_cell,
            "area_m2": round(float(g["region"].sum()) * res * res, 1),
            "wall_m2": round(float(g["wall"].sum()) * res * res, 1)}
    return buf.getvalue(), meta


def make(p: np.ndarray, n: np.ndarray | None = None, **kw) -> tuple[bytes, dict]:
    """z-up points (floor at 0) [+ normals] -> (PNG bytes, meta)."""
    rk = {key: kw.pop(key) for key in ("px_per_cell", "supersample", "pad_cells") if key in kw}
    return render(layers(p, n, **kw), **rk)


def normals_or_none(scale: np.ndarray, quat: np.ndarray) -> np.ndarray | None:
    """Gaussian normals (splat frame), or None when the gaussians are not disc-like (no usable normal)."""
    from .source import normals
    n, flat = normals(scale, quat)
    return n if np.median(flat) < 0.8 else None
