"""Cameras (app plan section 13), lifting onto the splat, frame-pose alignment."""
import io

import numpy as np
import pytest
from PIL import Image

from inspection.lifting import Camera, is_visible, lift, read_ply_points, visible_points
from localization.alignment import alignment_from_frame_poses
from localization.cameras import GL_TO_CV, Frame, Intrinsics, c2w_to_tq, to_cv, upright

from .conftest import noise_jpeg, wall, write_3dgs_ply


def project(c2w, K: Intrinsics, p):
    w2c = np.linalg.inv(c2w)
    pc = w2c[:3, :3] @ p + w2c[:3, 3]
    return np.array([K.fx * pc[0] / pc[2] + K.cx, K.fy * pc[1] / pc[2] + K.cy])


def test_portrait_rotation_matches_app_plan():
    """Projecting with (C_portrait, K') gives (H - v, u) of the projection with (C_cv, K)."""
    rng = np.random.default_rng(0)
    K = Intrinsics(1400, 1390, 960, 720, 1920, 1440)
    a = rng.normal(size=3)
    R, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    R *= np.sign(np.linalg.det(R))
    C_arkit = np.eye(4)
    C_arkit[:3, :3], C_arkit[:3, 3] = R, a
    buf = io.BytesIO()
    Image.new("RGB", (K.width, K.height)).save(buf, format="JPEG")
    raw = Frame("f", buf.getvalue(), K, to_cv(C_arkit))
    up = upright(raw, "portrait")
    assert (up.K.width, up.K.height) == (1440, 1920)
    assert Image.open(io.BytesIO(up.image)).size == (1440, 1920)
    p = raw.c2w[:3, :3] @ np.array([0.1, -0.2, 2.0]) + raw.c2w[:3, 3]   # a point in front of the camera
    u, v = project(raw.c2w, K, p)
    u2, v2 = project(up.c2w, up.K, p)
    assert np.allclose([u2, v2], [K.height - v, u], atol=1e-6)


def test_quaternion_xyzw():
    a = np.radians(90)
    c2w = np.eye(4)
    c2w[:3, :3] = [[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]]
    c2w[:3, 3] = [1, 2, 3]
    t, q = c2w_to_tq(c2w)
    assert t == [1, 2, 3] and np.allclose(q, [0, 0, np.sin(a / 2), np.cos(a / 2)], atol=1e-6)


CAM = {"convention": "c2w_opencv", "matrix": np.eye(4).tolist(), "fx": 200, "fy": 200, "cx": 160, "cy": 120,
       "width": 320, "height": 240}


def test_lift_area_length_and_occlusion():
    front, back = wall(3.0), wall(6.0)
    xyz = np.vstack([front, back])
    cam = Camera.from_dict(CAM)
    vis = visible_points(xyz, cam)
    # 64 x 16 px box in the center: at 3 m with f=200 -> 0.96 m x 0.24 m
    box = [[[128, 112], [192, 112], [192, 128], [128, 128]]]
    lf = lift(box, (320, 240), vis, cam, xyz)
    assert (lf.indices < len(front)).all(), "points behind the wall must not be selected"
    assert np.allclose(lf.centroid, [0, 0, 3], atol=0.03)
    assert np.allclose(lf.normal, [0, 0, -1], atol=1e-3)
    assert lf.area_m2 == pytest.approx(0.96 * 0.24, rel=0.02)
    assert lf.length_m == pytest.approx(0.96, rel=0.06)
    assert is_visible([0, 0, 3], vis, xyz) and not is_visible([0, 0, 6], vis, xyz)


def test_arkit_convention_equals_opengl():
    c = Camera.from_dict({**CAM, "convention": "arkit", "matrix": (np.eye(4) @ GL_TO_CV).tolist()})
    assert np.allclose(c.R, np.eye(3))


def test_ply_opacity(tmp_path):
    xyz = wall(3.0, 0.5, 0.5)
    logit = np.full(len(xyz), 4.0)
    logit[::2] = -6.0
    write_3dgs_ply(tmp_path / "s.ply", xyz, logit)
    rxyz, op = read_ply_points(tmp_path / "s.ply")
    assert np.allclose(rxyz, xyz, atol=1e-6) and op.min() < 0.01 and op.max() > 0.98


def test_frame_alignment_rejects_outlier():
    rng = np.random.default_rng(1)
    T = np.eye(4)
    T[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    T[:3, 3] = [2, -1, 0.5]
    dev = []
    for _ in range(5):
        C = np.eye(4)
        C[:3, 3] = rng.normal(size=3)
        dev.append(C)
    mp = [T @ C for C in dev]
    mp[2] = mp[2].copy()
    mp[2][:3, 3] += [1.0, 0, 0]           # one bad VPS result
    al = alignment_from_frame_poses(dev, mp)
    assert al["n_inliers"] == 4 and 2 not in al["inlier_frames"]
    assert np.allclose(al["matrix"], T, atol=1e-6)
