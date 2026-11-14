"""A free, CPU-only visual positioning system on top of the map's training views and the splat.

Index (once per map):
  for every training view (image + intrinsics + pose in the splat frame)
    * SIFT keypoints + descriptors
    * 3D position of each keypoint from the splat: the opaque gaussian centers are projected into
      the view (nearest surface per pixel cell), giving a depth per keypoint -> back-projected
  -> one database of (descriptor, 3D point)

Query (per image):
  SIFT -> nearest neighbours in the database (FLANN) -> ratio test across *distinct* 3D points
  (the same wall corner seen from 20 views must not count as ambiguous) -> 2D-3D matches
  -> PnP + RANSAC -> refinement -> pose in the splat frame.

Multi-frame consensus with the ARKit poses happens in alignment.py, as for any VPS.
Good enough for a room or corridor with some texture; hloc_vps.py is the learned-feature upgrade.
"""

import io
import json
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .cameras import Frame, Intrinsics
from .map_views import load_views
from .vps import VpsResult, confidence_from_inliers

MAX_SIDE = 1024            # images are downscaled to this before feature extraction
MAX_FEATURES_INDEX = 2000  # per training view
MAX_FEATURES_QUERY = 4000
RATIO = 0.8
DISTINCT_M = 0.10          # neighbours closer than this in 3D are the same physical point
MIN_INLIERS = 25
REPROJ_PX = 4.0


def _gray(image_bytes: bytes, K: Intrinsics) -> tuple[np.ndarray, Intrinsics]:
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    if img.size != (K.width, K.height):
        raise ValueError(f"image is {img.size}, intrinsics are for {K.width}x{K.height}")
    s = min(1.0, MAX_SIDE / max(img.size))
    if s < 1.0:
        img = img.resize((round(img.width * s), round(img.height * s)), Image.Resampling.LANCZOS)
        K = Intrinsics(K.fx * s, K.fy * s, K.cx * s, K.cy * s, img.width, img.height)
    return np.asarray(img), K


def _sift(n: int):
    return cv2.SIFT_create(nfeatures=n)


def _depth_grid(points: np.ndarray, c2w: np.ndarray, K: Intrinsics, cell: int = 2):
    """Nearest-surface depth per `cell`-pixel cell from projected splat points (inf = unknown)."""
    w2c = np.linalg.inv(c2w)
    pc = points @ w2c[:3, :3].T + w2c[:3, 3]
    z = pc[:, 2]
    ok = z > 0.05
    pc, z = pc[ok], z[ok]
    u = K.fx * pc[:, 0] / z + K.cx
    v = K.fy * pc[:, 1] / z + K.cy
    ok = (u >= 0) & (u < K.width) & (v >= 0) & (v < K.height)
    u, v, z = u[ok], v[ok], z[ok]
    gw, gh = K.width // cell + 1, K.height // cell + 1
    grid = np.full(gw * gh, np.inf)
    np.minimum.at(grid, (v // cell).astype(np.int64) * gw + (u // cell).astype(np.int64), z)
    grid = grid.reshape(gh, gw)
    # fill single-cell holes between gaussians with the nearest neighbour depth
    pad = np.pad(grid, 1, constant_values=np.inf)
    neigh = np.min([pad[1 + dy:1 + dy + gh, 1 + dx:1 + dx + gw] for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)
    return np.where(np.isinf(grid), neigh, grid), cell


def _keypoints_3d(kps, desc, depth_grid, cell, c2w, K):
    if desc is None or not kps:
        return np.zeros((0, 128), np.float32), np.zeros((0, 3))
    uv = np.array([k.pt for k in kps])
    gi = (uv[:, 1] // cell).astype(np.int64).clip(0, depth_grid.shape[0] - 1)
    gj = (uv[:, 0] // cell).astype(np.int64).clip(0, depth_grid.shape[1] - 1)
    z = depth_grid[gi, gj]
    ok = np.isfinite(z)
    uv, z, desc = uv[ok], z[ok], desc[ok]
    cam = np.stack([(uv[:, 0] - K.cx) / K.fx * z, (uv[:, 1] - K.cy) / K.fy * z, z], axis=1)
    world = cam @ c2w[:3, :3].T + c2w[:3, 3]
    return desc.astype(np.float32), world


class SiftVPS:
    name = "sift"

    def __init__(self, index_root):
        self.root = Path(index_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict = {}
        self._lock = threading.Lock()

    def _dir(self, map_id: str) -> Path:
        return self.root / map_id

    def has_index(self, map_id: str) -> bool:
        return (self._dir(map_id) / "index.json").is_file()

    # ------------------------------------------------------------ index

    def build_index(self, map_id: str, views_dir, views_to_map=None, map_xyz: np.ndarray | None = None) -> dict:
        if map_xyz is None or len(map_xyz) == 0:
            raise ValueError("the SIFT VPS needs the splat's gaussian centers for keypoint depth")
        image_root, views = load_views(Path(views_dir), views_to_map)
        sift = _sift(MAX_FEATURES_INDEX)
        all_desc, all_pts, all_view = [], [], []
        per_view = []
        for i, v in enumerate(views):
            gray, K = _gray((image_root / v.name).read_bytes(), v.K)
            kps, desc = sift.detectAndCompute(gray, None)
            grid, cell = _depth_grid(map_xyz, v.c2w, K)
            d, p = _keypoints_3d(kps, desc, grid, cell, v.c2w, K)
            all_desc.append(d)
            all_pts.append(p)
            all_view.append(np.full(len(d), i, np.int32))
            per_view.append(len(d))
        desc = np.concatenate(all_desc) if all_desc else np.zeros((0, 128), np.float32)
        if len(desc) < 100:
            raise ValueError(f"only {len(desc)} keypoints with depth; check that views and splat share a frame")
        out = self._dir(map_id)
        out.mkdir(parents=True, exist_ok=True)
        np.savez(out / "index.npz", desc=desc, pts=np.concatenate(all_pts).astype(np.float32),
                 view=np.concatenate(all_view))
        info = {"method": "sift", "n_views": len(views), "n_points": int(len(desc)),
                "points_per_view_median": int(np.median(per_view)),
                "views_without_points": int(sum(n == 0 for n in per_view))}
        (out / "index.json").write_text(json.dumps(info))
        with self._lock:
            self._cache.pop(map_id, None)
        return info

    def _load(self, map_id: str):
        with self._lock:
            if map_id not in self._cache:
                d = np.load(self._dir(map_id) / "index.npz")
                matcher = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 64})
                matcher.add([d["desc"]])
                matcher.train()
                self._cache[map_id] = (matcher, d["pts"].astype(np.float64))
            return self._cache[map_id]

    # ------------------------------------------------------------ query

    def localize(self, map_id: str, frames: list[Frame]) -> list[VpsResult | None]:
        if not self.has_index(map_id):
            raise RuntimeError(f"no VPS index for map {map_id}")
        matcher, pts = self._load(map_id)
        sift = _sift(MAX_FEATURES_QUERY)
        return [self._localize_one(f, sift, matcher, pts) for f in frames]

    def _localize_one(self, f: Frame, sift, matcher, pts) -> VpsResult | None:
        gray, K = _gray(f.image, f.K)
        kps, desc = sift.detectAndCompute(gray, None)
        if desc is None or len(kps) < MIN_INLIERS:
            return None
        knn = matcher.knnMatch(desc.astype(np.float32), k=6)
        img_pts, obj_pts = [], []
        for q, ms in enumerate(knn):
            if not ms:
                continue
            best = ms[0]
            p0 = pts[best.trainIdx]
            second = next((m for m in ms[1:] if np.linalg.norm(pts[m.trainIdx] - p0) > DISTINCT_M), None)
            if second is not None and best.distance >= RATIO * second.distance:
                continue
            img_pts.append(kps[q].pt)
            obj_pts.append(p0)
        if len(img_pts) < MIN_INLIERS:
            return None
        img_pts = np.asarray(img_pts, np.float64)
        obj_pts = np.asarray(obj_pts, np.float64)
        Kmat = np.array([[K.fx, 0, K.cx], [0, K.fy, K.cy], [0, 0, 1]])
        ok, rvec, tvec, inl = cv2.solvePnPRansac(obj_pts, img_pts, Kmat, None, iterationsCount=5000,
                                                 reprojectionError=REPROJ_PX, confidence=0.9999,
                                                 flags=cv2.SOLVEPNP_SQPNP)
        if not ok or inl is None or len(inl) < MIN_INLIERS:
            return None
        inl = inl.ravel()
        rvec, tvec = cv2.solvePnPRefineLM(obj_pts[inl], img_pts[inl], Kmat, None, rvec, tvec)
        R, _ = cv2.Rodrigues(rvec)
        w2c = np.eye(4)
        w2c[:3, :3], w2c[:3, 3] = R, tvec.ravel()
        return VpsResult(c2w=np.linalg.inv(w2c), num_inliers=int(len(inl)),
                         confidence=confidence_from_inliers(len(inl)))
