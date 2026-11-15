"""Check a localized capture by eye: render the splat from the pose the backend computed and put it
next to the real image (photo + frames). If the pose is right, the two views line up.

    uv run python tools/pose_check.py <capture-id> [--splat data/splat_30000.ply] [--out data/pose_check]
Writes <out>/<id8>_photo.png and <id8>_frame<k>.png:  [real image | splat render | 50% blend].
The render uses gaussian centres as coloured discs (quick and approximate, not a full splat render)."""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlmodel import Session  # noqa: E402

from backend.storage import storage  # noqa: E402
from backend.db import engine  # noqa: E402
from backend.models import Capture  # noqa: E402
from elevmap import source  # noqa: E402


def render(P, rgb, scale_max, c2w, K, W, H):
    s = W / K["width"]
    fx, fy, cx, cy = K["fx"] * s, K["fy"] * s, K["cx"] * s, K["cy"] * s
    w2c = np.linalg.inv(c2w)
    pc = P @ w2c[:3, :3].T + w2c[:3, 3]
    z = pc[:, 2]
    ok = z > 0.15
    pc, z, col, sm = pc[ok], z[ok], rgb[ok], scale_max[ok]
    u = fx * pc[:, 0] / z + cx
    v = fy * pc[:, 1] / z + cy
    r = np.clip(np.round(2.0 * sm * fx / z), 1, 7).astype(int)
    keep = (u > -8) & (u < W + 8) & (v > -8) & (v < H + 8)
    u, v, z, col, r = u[keep], v[keep], z[keep], col[keep], r[keep]
    order = np.argsort(-z)                       # far -> near, near overwrites
    u, v, col, r = u[order], v[order], col[order], r[order]
    img = np.full((H, W, 3), 0.93)
    ui, vi = np.round(u).astype(int), np.round(v).astype(int)
    # paint in far->near order, one radius class at a time is wrong for overlap, so stamp by offset
    for off in range(-7, 8):
        for offy in range(-7, 8):
            m = (np.abs(off) <= r) & (np.abs(offy) <= r) & (off * off + offy * offy <= r * r + 1)
            if not m.any():
                continue
            x, y = ui[m] + off, vi[m] + offy
            inb = (x >= 0) & (x < W) & (y >= 0) & (y < H)
            img[y[inb], x[inb]] = col[m][inb]
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture_id")
    ap.add_argument("--splat", default="data/splat_30000.ply")
    ap.add_argument("--out", default="data/pose_check", type=Path)
    ap.add_argument("--width", type=int, default=420)
    ap.add_argument("--frames", type=int, default=3)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    with Session(engine) as s:
        cap = s.get(Capture, a.capture_id)
        loc = cap.localization or {}
        assert loc.get("camera"), "capture has no localized camera pose"
        T = np.asarray((loc.get("alignment") or {}).get("matrix", np.eye(4)))
        cams = [("photo", storage.local_path(cap.image_key), np.asarray(loc["camera"]["matrix"]), loc["camera"])]
        for k, f in enumerate(cap.frames[: a.frames]):
            if f.get("c2w") is not None and loc.get("method") == "vps_frames":
                cams.append((f"frame{k}", storage.local_path(f["image_key"]), T @ np.asarray(f["c2w"]), f["K"]))
    d = source.load_splat(a.splat, min_opacity=0.2, log=lambda m: None)
    P, rgb, sm = d["xyz"], d["rgb"], d["scale"].max(1)
    print(f"{len(P):,} gaussians; capture {a.capture_id[:8]} method {loc.get('method')} confidence {loc.get('confidence')}")
    for name, img_path, c2w, K in cams:
        im = Image.open(img_path).convert("RGB")
        H = round(a.width * K["height"] / K["width"])
        real = np.asarray(im.resize((a.width, H)))
        sp = render(P, rgb, sm, c2w, K, a.width, H)
        blend = (0.5 * real + 0.5 * sp).astype(np.uint8)
        out = a.out / f"{a.capture_id[:8]}_{name}.png"
        Image.fromarray(np.concatenate([real, sp, blend], axis=1)).save(out)
        print("wrote", out)


if __name__ == "__main__":
    main()
