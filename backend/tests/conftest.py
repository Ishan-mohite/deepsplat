import os
import tempfile

import numpy as np

_tmp = tempfile.mkdtemp(prefix="damage-backend-test-")
os.environ["DETECTOR"] = "fake"
os.environ["DATA_DIR"] = _tmp
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"


def plane(z: float, half_w: float, half_h: float, step: float = 0.05) -> np.ndarray:
    xs = np.arange(-half_w, half_w + 1e-9, step)
    ys = np.arange(-half_h, half_h + 1e-9, step)
    X, Y = np.meshgrid(xs, ys)
    return np.stack([X.ravel(), Y.ravel(), np.full(X.size, z)], axis=1)


def write_3dgs_ply(path, xyz: np.ndarray, opacity_logit: np.ndarray | None = None) -> None:
    """Minimal binary PLY with the property layout of a 3DGS export (subset)."""
    n = len(xyz)
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
             "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    arr = np.zeros(n, dtype=[(k, "<f4") for k in names])
    arr["x"], arr["y"], arr["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    arr["opacity"] = opacity_logit if opacity_logit is not None else 5.0
    arr["rot_0"] = 1.0
    header = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n
    header += "".join(f"property float {k}\n" for k in names) + "end_header\n"
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(arr.tobytes())


# ------------------------------------------------------------------ synthetic building scan

def _rect(origin, e1, e2, spacing, rng):
    origin, e1, e2 = map(np.asarray, (origin, e1, e2))
    n1 = max(2, int(np.linalg.norm(e1) / spacing))
    n2 = max(2, int(np.linalg.norm(e2) / spacing))
    a, b = rng.random((n1 * n2, 1)), rng.random((n1 * n2, 1))
    return origin + a * e1 + b * e2


def _box(lo, hi, spacing, rng, skip_bottom=True):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    faces = [
        _rect((x0, y0, z1), (dx, 0, 0), (0, dy, 0), spacing, rng),   # top
        _rect((x0, y0, z0), (dx, 0, 0), (0, 0, dz), spacing, rng),   # front
        _rect((x0, y1, z0), (dx, 0, 0), (0, 0, dz), spacing, rng),   # back
        _rect((x0, y0, z0), (0, dy, 0), (0, 0, dz), spacing, rng),   # left
        _rect((x1, y0, z0), (0, dy, 0), (0, 0, dz), spacing, rng),   # right
    ]
    if not skip_bottom:
        faces.append(_rect((x0, y0, z0), (dx, 0, 0), (0, dy, 0), spacing, rng))
    return np.vstack(faces)


def building_scan(spacing=0.02, seed=0, noise=0.003) -> np.ndarray:
    """L-shaped room (z-up, meters) with furniture so the geometry is not symmetric."""
    rng = np.random.default_rng(seed)
    H = 2.6
    parts = [
        _rect((0, 0, 0), (6, 0, 0), (0, 4, 0), spacing, rng),        # floor
        _rect((0, 0, H), (6, 0, 0), (0, 4, 0), spacing, rng),        # ceiling
        _rect((0, 0, 0), (6, 0, 0), (0, 0, H), spacing, rng),        # wall y=0
        _rect((0, 4, 0), (3.5, 0, 0), (0, 0, H), spacing, rng),      # wall y=4 (left part)
        _rect((0, 0, 0), (0, 4, 0), (0, 0, H), spacing, rng),        # wall x=0
        _rect((6, 0, 0), (0, 2.5, 0), (0, 0, H), spacing, rng),      # wall x=6 (short)
        _rect((3.5, 2.5, 0), (0, 1.5, 0), (0, 0, H), spacing, rng),  # L notch wall
        _rect((3.5, 2.5, 0), (2.5, 0, 0), (0, 0, H), spacing, rng),  # L notch wall
        _box((0.1, 0.1, 0), (1.3, 0.7, 2.0), spacing, rng),          # cabinet
        _box((2.0, 1.2, 0), (3.2, 2.0, 0.75), spacing, rng),         # table
        _box((4.8, 0.6, 0), (5.1, 0.9, H), spacing, rng),            # column
        _box((0.1, 3.2, 0), (0.6, 3.9, 1.1), spacing, rng),          # shelf
    ]
    pts = np.vstack(parts)
    return pts + rng.normal(0, noise, pts.shape)


def yaw_translate(yaw_deg: float, t) -> np.ndarray:
    a = np.radians(yaw_deg)
    T = np.eye(4)
    T[:2, :2] = [[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]
    T[:3, 3] = t
    return T


def arkit_capture(T_arkit_to_scene: np.ndarray, center, radius=2.5, seed=1) -> np.ndarray:
    """Partial, independently sampled, noisier view of the scan, expressed in ARKit coordinates."""
    scan = building_scan(spacing=0.03, seed=seed, noise=0.006)
    near = np.linalg.norm(scan[:, :2] - np.asarray(center)[:2], axis=1) < radius
    pts = scan[near]
    hom = np.c_[pts, np.ones(len(pts))] @ np.linalg.inv(T_arkit_to_scene).T
    return hom[:, :3]


def write_xyz_ply(path, xyz: np.ndarray) -> None:
    arr = np.asarray(xyz, dtype="<f4")
    header = (f"ply\nformat binary_little_endian 1.0\nelement vertex {len(arr)}\n"
              "property float x\nproperty float y\nproperty float z\nend_header\n")
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(arr.tobytes())
