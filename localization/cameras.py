"""Camera conventions of the iOS capture app (docs/PLAN.md on mobile-recorder-app, section 13).

The app stores every frame raw: image in sensor orientation (landscape), ARKit camera->world
(row-major, camera +X right / +Y up / looking along -Z), intrinsics for the stored image size,
interface orientation ("portrait"). Everything is converted here, once:

* `to_cv`:     ARKit camera->world -> OpenCV axes (x right, y down, z forward):  C_cv = C · diag(1,-1,-1,1)
* `upright`:   rotate a portrait capture 90° clockwise so it is upright (for SAM 3, VPS, display),
               with K' and C_portrait = C_cv · R_p
Internally the backend always uses camera->world in OpenCV axes ("c2w_opencv").
"""

import io
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

GL_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])
R_P = np.array([[0, 1, 0, 0], [-1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float64)


@dataclass
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    @classmethod
    def from_dict(cls, d: dict) -> "Intrinsics":
        return cls(float(d["fx"]), float(d["fy"]), float(d["cx"]), float(d["cy"]),
                   int(d["width"]), int(d["height"]))

    def to_dict(self) -> dict:
        return {"fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy,
                "width": self.width, "height": self.height}


@dataclass
class Frame:
    """One image with its intrinsics and (optional) device-tracking pose, c2w in OpenCV axes."""
    name: str
    image: bytes
    K: Intrinsics
    c2w: np.ndarray | None          # device tracking frame (ARKit session), OpenCV axes
    t: float | None = None
    tracking_state: str = "normal"

    def camera_dict(self, c2w: np.ndarray | None = None) -> dict:
        m = self.c2w if c2w is None else c2w
        return {"convention": "c2w_opencv", "matrix": np.asarray(m).round(6).tolist(), **self.K.to_dict()}


def matrix_from_row_major(values) -> np.ndarray:
    a = np.asarray(values, dtype=np.float64)
    if a.shape == (16,):
        a = a.reshape(4, 4)
    if a.shape != (4, 4):
        raise ValueError("camera_to_world must be 16 numbers (row-major) or 4x4")
    return a


def to_cv(c2w_arkit: np.ndarray) -> np.ndarray:
    return np.asarray(c2w_arkit, dtype=np.float64) @ GL_TO_CV


def upright(frame: Frame, interface_orientation: str) -> Frame:
    """Rotate a raw sensor-orientation frame so it is upright for the given UI orientation."""
    if interface_orientation in ("landscape_right", "landscapeRight", "landscape", None, ""):
        return frame
    if interface_orientation != "portrait":
        raise ValueError(f"unsupported interface_orientation {interface_orientation!r}")
    img = Image.open(io.BytesIO(frame.image))
    W, H = img.size
    if (W, H) != (frame.K.width, frame.K.height):
        # intrinsics are for the stored size; scale if the JPEG differs (should not happen)
        s = W / frame.K.width
        K = Intrinsics(frame.K.fx * s, frame.K.fy * s, frame.K.cx * s, frame.K.cy * s, W, H)
    else:
        K = frame.K
    rotated = img.transpose(Image.Transpose.ROTATE_270)   # 90° clockwise
    buf = io.BytesIO()
    rotated.convert("RGB").save(buf, format="JPEG", quality=95)
    K2 = Intrinsics(fx=K.fy, fy=K.fx, cx=H - K.cy, cy=K.cx, width=H, height=W)
    c2w = None if frame.c2w is None else frame.c2w @ R_P
    return replace(frame, image=buf.getvalue(), K=K2, c2w=c2w)


def c2w_to_tq(c2w: np.ndarray) -> tuple[list[float], list[float]]:
    """Camera position and orientation quaternion [x, y, z, w] (as in the README record)."""
    R = np.asarray(c2w)[:3, :3]
    t = np.asarray(c2w)[:3, 3]
    tr = np.trace(R)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w, x, y, z = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w, x, y, z = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w, x, y, z = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    return [round(float(v), 5) for v in t], [round(float(v), 6) for v in (x, y, z, w)]
