"""A textured synthetic room for VPS tests: ray-cast renderer + surface points as 'splat'.

World: z up, meters, room [0,6] x [0,4] x [0,2.6]. Cameras: camera->world, OpenCV axes.
"""

import io

import numpy as np
from PIL import Image, ImageDraw

ROOM = (6.0, 4.0, 2.6)
PX_PER_M = 220


def _planes():
    X, Y, Z = ROOM
    return [  # origin, e1, e2
        ((0, 0, 0), (X, 0, 0), (0, Y, 0)),   # floor
        ((0, 0, Z), (X, 0, 0), (0, Y, 0)),   # ceiling
        ((0, 0, 0), (X, 0, 0), (0, 0, Z)),   # wall y=0
        ((0, Y, 0), (X, 0, 0), (0, 0, Z)),   # wall y=Y
        ((0, 0, 0), (0, Y, 0), (0, 0, Z)),   # wall x=0
        ((X, 0, 0), (0, Y, 0), (0, 0, Z)),   # wall x=X
    ]


def _texture(w: int, h: int, rng) -> np.ndarray:
    img = Image.new("RGB", (w, h), tuple(int(c) for c in rng.integers(90, 170, 3)))
    d = ImageDraw.Draw(img)
    for _ in range(int(w * h / 900)):
        x, y = rng.integers(0, w), rng.integers(0, h)
        s = int(rng.integers(6, 45))
        col = tuple(int(c) for c in rng.integers(0, 256, 3))
        kind = rng.integers(0, 4)
        if kind == 0:
            d.rectangle([x, y, x + s, y + int(s * rng.uniform(0.4, 1.6))], fill=col)
        elif kind == 1:
            d.ellipse([x, y, x + s, y + s], fill=col)
        elif kind == 2:
            d.line([x, y, x + int(rng.integers(-60, 60)), y + int(rng.integers(-60, 60))], fill=col, width=3)
        else:
            d.polygon([(x, y), (x + s, y + int(s / 3)), (x + int(s / 2), y + s)], fill=col)
    return np.asarray(img)


class Room:
    def __init__(self, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.planes = []
        for o, e1, e2 in _planes():
            o, e1, e2 = map(lambda a: np.asarray(a, float), (o, e1, e2))
            tw, th = int(np.linalg.norm(e1) * PX_PER_M), int(np.linalg.norm(e2) * PX_PER_M)
            self.planes.append((o, e1, e2, _texture(tw, th, rng)))

    def render(self, c2w: np.ndarray, K: dict) -> bytes:
        W, H = K["width"], K["height"]
        u, v = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
        d_cam = np.stack([(u - K["cx"]) / K["fx"], (v - K["cy"]) / K["fy"], np.ones_like(u)], -1).reshape(-1, 3)
        d = d_cam @ c2w[:3, :3].T
        c = c2w[:3, 3]
        best_t = np.full(len(d), np.inf)
        out = np.zeros((len(d), 3), np.uint8)
        for o, e1, e2, tex in self.planes:
            n = np.cross(e1, e2)
            denom = d @ n
            with np.errstate(divide="ignore", invalid="ignore"):
                t = ((o - c) @ n) / denom
            hit = c + t[:, None] * d
            a = (hit - o) @ e1 / (e1 @ e1)
            b = (hit - o) @ e2 / (e2 @ e2)
            ok = (t > 1e-6) & (t < best_t) & (a >= 0) & (a <= 1) & (b >= 0) & (b <= 1)
            th, tw = tex.shape[:2]
            xi = (a[ok] * (tw - 1)).astype(int)
            yi = ((1 - b[ok]) * (th - 1)).astype(int)
            out[ok] = tex[yi, xi]
            best_t[ok] = t[ok]
        buf = io.BytesIO()
        Image.fromarray(out.reshape(H, W, 3)).save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    def surface_points(self, step: float = 0.03) -> np.ndarray:
        pts = []
        for o, e1, e2, _ in self.planes:
            a = np.arange(0, 1, step / np.linalg.norm(e1))
            b = np.arange(0, 1, step / np.linalg.norm(e2))
            A, B = np.meshgrid(a, b)
            pts.append(o + A.reshape(-1, 1) * e1 + B.reshape(-1, 1) * e2)
        return np.vstack(pts)


def look(position, yaw_deg: float, pitch_deg: float = 0.0) -> np.ndarray:
    """camera->world (OpenCV axes) at `position`, looking along yaw (from +x, ccw) and pitch (up)."""
    y, p = np.radians(yaw_deg), np.radians(pitch_deg)
    f = np.array([np.cos(y) * np.cos(p), np.sin(y) * np.cos(p), np.sin(p)])
    r = np.cross(f, [0, 0, 1.0])
    r /= np.linalg.norm(r)
    c2w = np.eye(4)
    c2w[:3, 0], c2w[:3, 1], c2w[:3, 2], c2w[:3, 3] = r, np.cross(f, r), f, position
    return c2w


def training_poses() -> list[np.ndarray]:
    """48 views: 6 positions x 8 headings, pitch alternating 0 / -20 degrees."""
    return [look((x, y, 1.5), yaw, -20 * ((yaw // 45) % 2))
            for x in (1.5, 3.0, 4.5) for y in (1.3, 2.7)
            for yaw in range(0, 360, 45)]


def views_zip(room: Room, poses: list[np.ndarray], K: dict) -> bytes:
    """Training views in nerfstudio layout (transforms.json, OpenGL camera->world)."""
    import json
    import zipfile
    gl = np.diag([1.0, -1.0, -1.0, 1.0])
    frames = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, c2w in enumerate(poses):
            name = f"images/view_{i:03d}.jpg"
            z.writestr(name, room.render(c2w, K))
            frames.append({"file_path": name, "transform_matrix": (c2w @ gl).tolist()})
        z.writestr("transforms.json", json.dumps({
            "fl_x": K["fx"], "fl_y": K["fy"], "cx": K["cx"], "cy": K["cy"], "w": K["width"], "h": K["height"],
            "frames": frames}))
    return buf.getvalue()
