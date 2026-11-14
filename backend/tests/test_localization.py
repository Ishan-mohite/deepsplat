import numpy as np
import pytest

from app.localization import Camera, localize, read_ply_points, visible_points

from .conftest import plane, write_3dgs_ply

CAM = {"fx": 100, "fy": 100, "cx": 50, "cy": 50, "width": 100, "height": 100}
# square in the center of a 200x200 detection image -> pixels 40..60 of the 100x100 camera
SQUARE_200 = [[[80, 80], [120, 80], [120, 120], [80, 120]]]


def scene():
    front = plane(5.0, 2, 2)     # wall 5 units in front of the camera
    back = plane(10.0, 2, 2)     # occluded surface behind it
    return np.vstack([front, back]), len(front)


@pytest.mark.parametrize("cam_dict", [
    {"convention": "w2c_opencv", "matrix": np.eye(4).tolist(), **CAM},
    {"convention": "colmap", "qvec": [1, 0, 0, 0], "tvec": [0, 0, 0], **CAM},
])
def test_mask_projection_selects_visible_points_in_mask(cam_dict):
    xyz, n_front = scene()
    cam = Camera.from_dict(cam_dict)
    vis = visible_points(xyz, cam)
    loc = localize(SQUARE_200, (200, 200), vis, cam, xyz)

    assert loc is not None
    assert (loc.indices < n_front).all(), "occluded back-plane points must not be selected"
    pts = xyz[loc.indices]
    assert np.allclose(pts[:, 2], 5.0)
    assert np.abs(pts[:, :2]).max() <= 0.5 + 1e-6      # u = 100*x/5 + 50 in [40, 60]
    assert np.allclose(loc.centroid, [0, 0, 5], atol=0.05)
    assert np.allclose(loc.normal, [0, 0, -1], atol=1e-3)  # facing the camera
    assert loc.mean_depth == pytest.approx(5.0)


def test_c2w_opengl_convention():
    # OpenGL camera at origin looks down -z: put the wall at z = -5
    xyz = plane(-5.0, 2, 2)
    cam = Camera.from_dict({"convention": "c2w_opengl", "matrix": np.eye(4).tolist(), **CAM})
    loc = localize(SQUARE_200, (200, 200), visible_points(xyz, cam), cam, xyz)
    assert loc is not None
    assert np.allclose(loc.centroid, [0, 0, -5], atol=0.05)
    assert np.allclose(loc.normal, [0, 0, 1], atol=1e-3)


def test_translated_camera():
    # camera moved to x=+1 (world->camera t = -R @ C)
    xyz = plane(5.0, 3, 2)
    cam = Camera.from_dict({"convention": "colmap", "qvec": [1, 0, 0, 0], "tvec": [-1, 0, 0], **CAM})
    loc = localize(SQUARE_200, (200, 200), visible_points(xyz, cam), cam, xyz)
    assert np.allclose(loc.centroid, [1, 0, 5], atol=0.05)


def test_mask_outside_scene_returns_none():
    xyz = plane(5.0, 0.2, 0.2)  # tiny patch in the image center
    cam = Camera.from_dict({"convention": "colmap", "qvec": [1, 0, 0, 0], "tvec": [0, 0, 0], **CAM})
    corner = [[[0, 0], [20, 0], [20, 20], [0, 20]]]
    assert localize(corner, (200, 200), visible_points(xyz, cam), cam, xyz) is None


def test_ply_roundtrip_and_opacity_filter(tmp_path):
    xyz = plane(5.0, 1, 1)
    logits = np.full(len(xyz), 4.0)
    logits[: len(xyz) // 2] = -6.0            # transparent floaters
    p = tmp_path / "s.ply"
    write_3dgs_ply(p, xyz, logits)
    rxyz, op = read_ply_points(p)
    assert np.allclose(rxyz, xyz, atol=1e-6)
    assert op.min() < 0.01 and op.max() > 0.98   # logit -> sigmoid
    cam = Camera.from_dict({"convention": "colmap", "qvec": [1, 0, 0, 0], "tvec": [0, 0, 0], **CAM})
    idx, *_ = visible_points(rxyz, cam, op)
    assert (idx >= len(xyz) // 2).all()


def test_dedupe_keeps_most_confident_overlapping_detection():
    from app.detection.base import dedupe, make_damage
    sq = lambda o: [[[o, o], [o + 100, o], [o + 100, o + 100], [o, o + 100]]]
    a = make_damage("crack", "crack", 0.9, sq(0), 1000, 1000)
    b = make_damage("peeling_paint", "damaged plaster", 0.5, sq(2), 1000, 1000)   # same region
    c = make_damage("mold", "mold", 0.4, sq(500), 1000, 1000)                      # elsewhere
    out = dedupe([b, c, a])
    assert [d.damage_class for d in out] == ["crack", "mold"]
