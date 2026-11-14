"""End to end with the real SIFT VPS: map upload (splat + views) -> index -> iOS capture zip with raw
portrait frames in an arbitrary ARKit frame -> damage placed on the right spot of the wall."""
import io
import json
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from inspection.detection import set_detector
from inspection.detection.fake import FakeDetector
from localization.cameras import GL_TO_CV, R_P
from localization.sift_vps import SiftVPS
from localization.vps import set_vps

from .conftest import write_3dgs_ply
from .synthetic_room import Room, look, views_zip

K_TRAIN = {"fx": 380.0, "fy": 380.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}
K_UP = {"fx": 380.0, "fy": 380.0, "cx": 240.0, "cy": 320.0, "width": 480, "height": 640}     # upright portrait
K_RAW = {"fx": 380.0, "fy": 380.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}    # sensor orientation
# ARKit session frame vs map: rotated 120° about the vertical and shifted (ARKit is y-up, map z-up)
a = np.radians(120)
T_DEV_TO_MAP = np.array([[np.cos(a), 0, np.sin(a), 2.0], [np.sin(a), 0, -np.cos(a), 3.0],
                         [0, 1, 0, 0.3], [0, 0, 0, 1]])
assert np.isclose(np.linalg.det(T_DEV_TO_MAP[:3, :3]), 1)

PHOTO_POSE = look((3.0, 1.2, 1.4), 270, 0)          # close-up of the wall y=0, 1.2 m away
FRAME_POSES = [look((2.6 + 0.2 * i, 2.6, 1.5), 270, -5) for i in range(5)]   # stepping sideways, 2.6 m away


def rel(url):
    return url.replace("http://testserver", "")


def raw_capture(room: Room, c2w_map_up: np.ndarray) -> tuple[bytes, list]:
    """What the app stores: raw landscape JPEG + ARKit camera->world (row-major)."""
    up = Image.open(io.BytesIO(room.render(c2w_map_up, K_UP)))
    buf = io.BytesIO()
    up.transpose(Image.Transpose.ROTATE_90).save(buf, format="JPEG", quality=92)     # undo the app plan's rotation
    dev_up = np.linalg.inv(T_DEV_TO_MAP) @ c2w_map_up
    return buf.getvalue(), (dev_up @ np.linalg.inv(R_P) @ GL_TO_CV).ravel().tolist()


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    room = Room(seed=1)
    vps = SiftVPS(tmp_path_factory.mktemp("vps"))
    train = [look((x, y, 1.5), yaw, -15 * (i % 2)) for x in (1.5, 3.0, 4.5) for y in (1.3, 2.7)
             for i, yaw in enumerate((240, 270, 300))]
    ply = tmp_path_factory.mktemp("map") / "splat.ply"
    write_3dgs_ply(ply, room.surface_points())
    set_vps(vps)
    with TestClient(app) as c:
        r = c.post("/maps", data={"name": "VPS Room", "map_id": "vps-room"},
                   files={"splat": ("splat.ply", ply.read_bytes()),
                          "views": ("views.zip", views_zip(room, train, K_TRAIN), "application/zip")})
        assert r.status_code == 202, r.text
        m = c.get("/maps/vps-room").json()
        assert m["status"] == "ready" and m["vps_status"] == "ready", m
        yield c, room, vps


def test_ios_capture_localized_by_own_vps(setup):
    c, room, vps = setup
    set_vps(vps)
    set_detector(FakeDetector())
    rec = lambda f, t, pose: {"file": f, "t": t, "camera_to_world": pose, "intrinsics": K_RAW,
                              "interface_orientation": "portrait", "tracking_state": "normal"}
    files, frames = {}, []
    for i, pose in enumerate(FRAME_POSES):
        img, arkit = raw_capture(room, pose)
        files[f"frames/{i + 1:06d}.jpg"] = img
        frames.append(rec(f"frames/{i + 1:06d}.jpg", 10.0 + 0.6 * i, arkit))
    photo_img, photo_arkit = raw_capture(room, PHOTO_POSE)
    manifest = {"schema_version": 1, "capture_id": "vps-1", "map_id": "vps-room",
                "video": {"frames": frames}, "photo": rec("photo.jpg", 13.5, photo_arkit)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("capture.json", json.dumps(manifest))
        z.writestr("photo.jpg", photo_img)
        for k, v in files.items():
            z.writestr(k, v)

    up = c.post("/v1/captures", json={"capture_id": "vps-1", "map_id": "vps-room"}).json()
    assert c.put(rel(up["upload_url"]), content=buf.getvalue()).status_code == 200
    c.post("/v1/captures/vps-1/complete")
    cap = c.get("/captures/vps-1").json()
    assert cap["status"] == "done", (cap["status"], cap["error"], cap["localization"])
    loc = cap["localization"]
    assert loc["method"] == "vps_frames" and loc["details"]["alignment"]["n_inliers"] >= 4
    assert np.allclose(loc["camera_pose"]["t"], PHOTO_POSE[:3, 3], atol=0.03)
    obs = cap["observations"][0]
    assert np.allclose(obs["position_m"], [3.0, 0.0, 1.4], atol=0.03)      # the spot on the wall


def test_localize_multi_endpoint(setup):
    c, room, vps = setup
    set_vps(vps)
    meta = {"map_id": "vps-room", "frames": []}
    files = []
    for pose in FRAME_POSES[:4]:
        img, arkit = raw_capture(room, pose)
        files.append(("images", ("f.jpg", img, "image/jpeg")))
        meta["frames"].append({"intrinsics": K_RAW, "camera_to_world": arkit, "interface_orientation": "portrait"})
    r = c.post("/localize/multi", files=files, data={"metadata": json.dumps(meta)})
    assert r.status_code == 200, r.text
    out = r.json()
    T = np.asarray(out["device_to_map"])
    assert out["n_localized"] == 4 and out["confidence"] > 0.5
    assert np.linalg.norm(T[:3, 3] - T_DEV_TO_MAP[:3, 3]) < 0.03
    R = T[:3, :3].T @ T_DEV_TO_MAP[:3, :3]
    assert np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1))) < 0.5
