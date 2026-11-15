"""The SIFT VPS on a rendered room: index from training views + splat points, localize new views."""
import io
import zipfile

import numpy as np
import pytest

from localization.cameras import Frame, Intrinsics
from localization.sift_vps import SiftVPS

from .synthetic_room import Room, look, training_poses, views_zip

K = {"fx": 380.0, "fy": 380.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}


def rot_err_deg(A, B):
    R = A[:3, :3].T @ B[:3, :3]
    return np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))


@pytest.fixture(scope="module")
def vps(tmp_path_factory):
    room = Room(seed=0)
    d = tmp_path_factory.mktemp("views")
    with zipfile.ZipFile(io.BytesIO(views_zip(room, training_poses(), K))) as z:
        z.extractall(d)
    v = SiftVPS(tmp_path_factory.mktemp("index"))
    info = v.build_index("room", d, map_xyz=room.surface_points())
    assert info["n_views"] == 48 and info["views_without_points"] == 0
    return v, room


@pytest.mark.parametrize("pos,yaw,pitch", [
    ((2.2, 2.0, 1.4), 20, -10),      # between training positions
    ((3.7, 1.9, 1.7), 200, 5),
    ((1.1, 3.1, 1.2), -70, -25),
    ((5.0, 1.0, 1.6), 135, 0),
])
def test_localizes_new_views(vps, pos, yaw, pitch):
    v, room = vps
    truth = look(pos, yaw, pitch)
    r = v.localize("room", [Frame("q", room.render(truth, K), Intrinsics(**K), None)])[0]
    assert r is not None, "not localized"
    assert np.linalg.norm(r.c2w[:3, 3] - truth[:3, 3]) < 0.03
    assert rot_err_deg(r.c2w, truth) < 0.5
    assert r.num_inliers >= 50


def test_blank_image_is_not_localized(vps):
    v, _ = vps
    blank = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (640, 480), (128, 128, 128)).save(blank, format="JPEG")
    assert v.localize("room", [Frame("q", blank.getvalue(), Intrinsics(**K), None)])[0] is None
