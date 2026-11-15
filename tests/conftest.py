import io
import os
import tempfile

import numpy as np
import pytest
from PIL import Image

_tmp = tempfile.mkdtemp(prefix="deepsplat-test-")
os.environ.update({
    "DETECTOR": "fake",
    "VPS": "none",
    "DATA_DIR": _tmp,
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "PUBLIC_BASE_URL": "http://testserver",
    "API_TOKEN": "",
})


def wall(z: float = 3.0, half_w: float = 3.0, half_h: float = 2.0, step: float = 0.02) -> np.ndarray:
    """Points of a wall facing an OpenCV camera at the origin (camera looks along +z)."""
    xs = np.arange(-half_w, half_w + 1e-9, step)
    ys = np.arange(-half_h, half_h + 1e-9, step)
    X, Y = np.meshgrid(xs, ys)
    return np.stack([X.ravel(), Y.ravel(), np.full(X.size, z)], axis=1)


def write_3dgs_ply(path, xyz: np.ndarray, opacity_logit: np.ndarray | None = None) -> None:
    """Minimal binary PLY with the property layout of a 3DGS export (subset)."""
    names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
             "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    arr = np.zeros(len(xyz), dtype=[(k, "<f4") for k in names])
    arr["x"], arr["y"], arr["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    arr["opacity"] = opacity_logit if opacity_logit is not None else 5.0
    arr["rot_0"] = 1.0
    header = f"ply\nformat binary_little_endian 1.0\nelement vertex {len(xyz)}\n"
    header += "".join(f"property float {k}\n" for k in names) + "end_header\n"
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(arr.tobytes())


def noise_jpeg(w: int, h: int, seed: int = 0) -> bytes:
    """Textured image (passes the sharpness / brightness gate)."""
    rng = np.random.default_rng(seed)
    img = Image.fromarray(rng.integers(40, 220, (h, w, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def reset_plugins():
    from inspection.detection import set_detector
    from localization.vps import set_vps
    yield
    set_detector(None)
    set_vps(None)
