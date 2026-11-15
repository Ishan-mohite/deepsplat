"""The map's training views: the images the splat was trained on, with their camera poses.

Accepted layouts of an uploaded views folder / zip:

* nerfstudio:  transforms.json (+ images): frames[].file_path, frames[].transform_matrix
               (camera->world, OpenGL axes), intrinsics fl_x/fl_y/cx/cy/w/h global or per frame
* COLMAP text: sparse/0/{cameras,images}.txt or {cameras,images}.txt (+ images/)

The poses MUST be in the same coordinate frame (and metric scale) as the splat .ply. If they are
not (e.g. nerfstudio's dataparser normalization), pass `views_to_map` (4x4) with the map.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .cameras import GL_TO_CV, Intrinsics


@dataclass
class View:
    name: str           # image path relative to the image root
    K: Intrinsics
    c2w: np.ndarray     # camera->world in the map frame, OpenCV axes


def _qvec_to_R(q):
    qw, qx, qy, qz = q
    return np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ])


def _R_to_qvec(R):
    tr = np.trace(R)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        return [0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    i = int(np.argmax(np.diag(R)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = np.sqrt(1.0 + R[i, i] - R[j, j] - R[k, k]) * 2
    q = [0.0, 0.0, 0.0, 0.0]
    q[0] = (R[k, j] - R[j, k]) / s
    q[1 + i] = 0.25 * s
    q[1 + j] = (R[j, i] + R[i, j]) / s
    q[1 + k] = (R[k, i] + R[i, k]) / s
    return q


def _from_transforms_json(path: Path) -> tuple[Path, list[View]]:
    data = json.loads(path.read_text())
    views = []
    for f in data["frames"]:
        g = {**data, **f}
        K = Intrinsics(float(g["fl_x"]), float(g.get("fl_y", g["fl_x"])), float(g["cx"]), float(g["cy"]),
                       int(g["w"]), int(g["h"]))
        c2w = np.asarray(f["transform_matrix"], dtype=np.float64) @ GL_TO_CV
        views.append(View(name=f["file_path"], K=K, c2w=c2w))
    return path.parent, views


def _from_colmap_text(model_dir: Path, root: Path) -> tuple[Path, list[View]]:
    cams = {}
    for line in (model_dir / "cameras.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        p = line.split()
        cid, model, w, h, params = int(p[0]), p[1], int(p[2]), int(p[3]), list(map(float, p[4:]))
        if model in ("PINHOLE", "OPENCV", "FULL_OPENCV"):
            fx, fy, cx, cy = params[:4]
        elif model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
            fx = fy = params[0]
            cx, cy = params[1:3]
        else:
            raise ValueError(f"unsupported COLMAP camera model {model}")
        cams[cid] = Intrinsics(fx, fy, cx, cy, w, h)
    views = []
    lines = [l for l in (model_dir / "images.txt").read_text().splitlines() if not l.startswith("#")]
    for line in lines[::2]:
        p = line.split()
        if len(p) < 10:
            continue
        R, t = _qvec_to_R(list(map(float, p[1:5]))), np.asarray(list(map(float, p[5:8])))
        w2c = np.eye(4)
        w2c[:3, :3], w2c[:3, 3] = R, t
        views.append(View(name=p[9], K=cams[int(p[8])], c2w=np.linalg.inv(w2c)))
    image_root = root / "images" if (root / "images").is_dir() else root
    return image_root, views


def load_views(root: Path, views_to_map: np.ndarray | None = None) -> tuple[Path, list[View]]:
    """Return (image root, views) from an extracted views folder."""
    root = Path(root)
    tj = next(iter(sorted(root.rglob("transforms.json"))), None)
    if tj is not None:
        image_root, views = _from_transforms_json(tj)
    else:
        imgs = next(iter(sorted(root.rglob("images.txt"))), None)
        if imgs is None:
            raise ValueError("views need transforms.json (nerfstudio) or a COLMAP text model")
        image_root, views = _from_colmap_text(imgs.parent, root)
    if views_to_map is not None:
        T = np.asarray(views_to_map, dtype=np.float64)
        for v in views:
            v.c2w = T @ v.c2w
    missing = [v.name for v in views if not (image_root / v.name).is_file()]
    if missing:
        raise ValueError(f"{len(missing)} view images missing, e.g. {missing[0]}")
    return image_root, views


def write_colmap_model(views: list[View], out_dir: Path) -> None:
    """Reference COLMAP model (known poses, no points) for hloc's triangulation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cam_lines, img_lines = [], []
    for i, v in enumerate(views, start=1):
        cam_lines.append(f"{i} PINHOLE {v.K.width} {v.K.height} {v.K.fx} {v.K.fy} {v.K.cx} {v.K.cy}")
        w2c = np.linalg.inv(v.c2w)
        q = _R_to_qvec(w2c[:3, :3])
        t = w2c[:3, 3]
        img_lines.append(f"{i} {' '.join(f'{x:.9f}' for x in q)} {' '.join(f'{x:.9f}' for x in t)} {i} {v.name}")
        img_lines.append("")
    (out_dir / "cameras.txt").write_text("\n".join(cam_lines) + "\n")
    (out_dir / "images.txt").write_text("\n".join(img_lines) + "\n")
    (out_dir / "points3D.txt").write_text("")
