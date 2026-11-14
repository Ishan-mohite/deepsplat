"""Top-down floor plan rendered from the splat (dollhouse cut), for the 2D unit view with pins.

Gaussian colors come from the 3DGS DC spherical-harmonic coefficients (f_dc_0..2). Everything
between the floor and `cut_m` above it is rendered from above (highest point per pixel), so walls
and furniture show while the ceiling is removed.
"""

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .placement import to_zup

SH_C0 = 0.28209479177387814


def read_ply_colors(path: Path) -> np.ndarray | None:
    """RGB 0..1 per vertex from f_dc_* (3DGS) or red/green/blue (point clouds), else None."""
    with open(path, "rb") as f:
        header, names, fmt, n = [], [], None, 0
        types = []
        in_vertex = False
        while True:
            line = f.readline().decode("ascii", "replace").strip()
            tok = line.split()
            if tok and tok[0] == "format":
                fmt = tok[1]
            elif tok and tok[0] == "element":
                in_vertex = tok[1] == "vertex"
                if in_vertex:
                    n = int(tok[2])
            elif tok and tok[0] == "property" and in_vertex:
                types.append(tok[1])
                names.append(tok[2])
            elif line == "end_header":
                break
        if fmt != "binary_little_endian":
            return None
        from .lifting import PLY_TYPES
        dtype = np.dtype([(nm, "<" + PLY_TYPES[t]) for nm, t in zip(names, types)])
        arr = np.frombuffer(f.read(dtype.itemsize * n), dtype=dtype, count=n)
    if {"f_dc_0", "f_dc_1", "f_dc_2"} <= set(names):
        rgb = 0.5 + SH_C0 * np.stack([arr["f_dc_0"], arr["f_dc_1"], arr["f_dc_2"]], axis=1)
        return np.clip(rgb, 0, 1)
    if {"red", "green", "blue"} <= set(names):
        return np.stack([arr["red"], arr["green"], arr["blue"]], axis=1) / 255.0
    return None


def render(xyz: np.ndarray, rgb: np.ndarray | None, *, up_axis: str = "z", floor_z: float,
           cut_m: float = 1.6, m_per_px: float = 0.02, margin_m: float = 0.3) -> tuple[bytes, dict]:
    """Return (PNG bytes, meta). meta maps floor coordinates to pixels:
    px = (x - origin_x) / m_per_px,  py = (origin_y - y) / m_per_px."""
    p = to_zup(xyz, up_axis)
    sel = (p[:, 2] > floor_z - 0.05) & (p[:, 2] < floor_z + cut_m)
    p = p[sel]
    col = rgb[sel] if rgb is not None else None
    lo, hi = p[:, :2].min(axis=0) - margin_m, p[:, :2].max(axis=0) + margin_m
    W = int(np.ceil((hi[0] - lo[0]) / m_per_px))
    H = int(np.ceil((hi[1] - lo[1]) / m_per_px))
    px = ((p[:, 0] - lo[0]) / m_per_px).astype(np.int64).clip(0, W - 1)
    py = ((hi[1] - p[:, 1]) / m_per_px).astype(np.int64).clip(0, H - 1)
    flat = py * W + px
    order = np.argsort(p[:, 2])                     # later (higher) points overwrite lower ones
    img = np.full((H * W, 3), 245, np.uint8)
    if col is None:
        h = (p[:, 2] - floor_z) / cut_m
        col = np.stack([0.35 + 0.4 * h] * 3, axis=1)
    img[flat[order]] = (np.clip(col[order], 0, 1) * 255).astype(np.uint8)
    filled = np.zeros(H * W, bool)
    filled[flat] = True
    img, filled = img.reshape(H, W, 3), filled.reshape(H, W)
    for _ in range(2):          # close 1-2 px gaps between gaussians with a neighbour's color
        for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            src = np.roll(np.roll(filled, dy, 0), dx, 1)
            take = ~filled & src
            img[take] = np.roll(np.roll(img, dy, 0), dx, 1)[take]
            filled |= take
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG", optimize=True)
    meta = {"m_per_px": m_per_px, "origin_x": round(float(lo[0]), 4), "origin_y": round(float(hi[1]), 4),
            "width": W, "height": H, "floor_z": floor_z, "cut_m": cut_m}
    return buf.getvalue(), meta


def to_px(floor_xy, meta: dict) -> list[float]:
    return [round((floor_xy[0] - meta["origin_x"]) / meta["m_per_px"], 1),
            round((meta["origin_y"] - floor_xy[1]) / meta["m_per_px"], 1)]


def with_pins(png: bytes, meta: dict, pins: list[tuple[list[float], tuple[int, int, int]]]) -> bytes:
    """Floor plan with colored pins (for the PDF report)."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    d = ImageDraw.Draw(img)
    r = max(6, img.width // 80)
    for xy, color in pins:
        x, y = to_px(xy, meta)
        d.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(255, 255, 255), width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
