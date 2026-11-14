import numpy as np
import pytest

from app.registration import ARKIT_TO_ZUP, from_correspondences, register, tilt_deg, transform_camera

from .conftest import arkit_capture, building_scan, yaw_translate


def rot_err_deg(A, B):
    R = A[:3, :3].T @ B[:3, :3]
    return np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))


SCENARIOS = [
    (37.0, (1.2, -0.4, 0.0), (3.0, 1.5)),
    (-120.0, (3.0, 2.0, 0.0), (4.5, 1.2)),
    (80.0, (0.0, 0.0, 0.0), (1.5, 1.5)),      # FPFH alone fails here
    (170.0, (2.0, 1.0, 0.0), (2.0, 3.0)),     # FPFH alone fails here
]


def pose_error(r, T_true):
    T = np.asarray(r.matrix)
    return np.linalg.norm(T[:3, 3] - T_true[:3, 3]), rot_err_deg(T, T_true)


@pytest.fixture(scope="module")
def scene():
    return building_scan()


@pytest.mark.parametrize("yaw,t,center", SCENARIOS)
def test_never_ok_when_wrong_without_hint(scene, yaw, t, center):
    """Feature matching alone may fail, but it must then say so (ok=False)."""
    T_true = yaw_translate(yaw, t) @ ARKIT_TO_ZUP
    r = register(arkit_capture(T_true, center), scene)
    dt, da = pose_error(r, T_true)
    if r.ok:
        assert dt < 0.03 and da < 1.0, (r, dt, da)


@pytest.mark.parametrize("yaw,t,center", SCENARIOS)
def test_hint_makes_alignment_reliable(scene, yaw, t, center):
    """With a rough position (here 1 m off, as if tapped on a floor plan) every scenario succeeds."""
    T_true = yaw_translate(yaw, t) @ ARKIT_TO_ZUP
    hint = (center[0] + 0.7, center[1] - 0.7, 1.3)
    r = register(arkit_capture(T_true, center), scene, hint_center=hint, hint_radius=2.5)
    dt, da = pose_error(r, T_true)
    assert r.ok, r
    assert dt < 0.03 and da < 1.0 and r.max_tilt_deg < 1.0


def test_icp_with_initial_guess(scene):
    T_true = yaw_translate(10.0, (0.5, 0.3, 0.0)) @ ARKIT_TO_ZUP
    cap = arkit_capture(T_true, (1.5, 1.5))
    guess = yaw_translate(13.0, (0.6, 0.2, 0.0)) @ ARKIT_TO_ZUP   # a few cm / degrees off
    r = register(cap, scene, init=guess)
    assert r.method == "icp" and r.ok
    assert np.linalg.norm(np.asarray(r.matrix)[:3, 3] - T_true[:3, 3]) < 0.02


def test_correspondences_kabsch():
    T_true = yaw_translate(55.0, (2, 1, 0.1)) @ ARKIT_TO_ZUP
    P = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], dtype=float)
    Q = (np.c_[P, np.ones(5)] @ T_true.T)[:, :3]
    r = from_correspondences(P, Q)
    assert np.allclose(r.matrix, T_true, atol=1e-6) and r.rmse < 1e-6


def test_transform_camera_and_tilt():
    T = yaw_translate(30.0, (1, 2, 0)) @ ARKIT_TO_ZUP
    assert tilt_deg(T) < 1e-6
    cam = {"convention": "arkit", "matrix": np.eye(4).tolist(), "fx": 1, "fy": 1, "cx": 0, "cy": 0,
           "width": 2, "height": 2}
    out = transform_camera(cam, T)
    assert out["convention"] == "c2w_opengl" and np.allclose(out["matrix"], T)
