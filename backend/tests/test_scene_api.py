"""Building scan flow: upload scan -> capture with ARKit pose + LiDAR geometry -> damage in scan coords."""
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.registration import ARKIT_TO_ZUP

from .conftest import arkit_capture, building_scan, write_xyz_ply, yaw_translate

W, H = 200, 150
T_TRUE = yaw_translate(37.0, (1.2, -0.4, 0.0)) @ ARKIT_TO_ZUP      # ARKit world -> scan

# photo taken from (3, 2, 1.5) in the scan, looking at the wall y=0 (OpenGL camera: looks down -z)
C2W_SCENE = np.array([[-1, 0, 0, 3.0], [0, 0, 1, 2.0], [0, 1, 0, 1.5], [0, 0, 0, 1]], dtype=float)
ARKIT_CAM = {"matrix": (np.linalg.inv(T_TRUE) @ C2W_SCENE).tolist(),
             "fx": 100, "fy": 100, "cx": W / 2, "cy": H / 2, "width": W, "height": H}
EXPECTED = [3.0, 0.0, 1.5]   # fake detection = image center -> straight ahead on the wall


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), (200, 190, 180)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def client_and_scene(tmp_path_factory):
    d = tmp_path_factory.mktemp("scene")
    scan, geom = d / "scan.ply", d / "geom.ply"
    write_xyz_ply(scan, building_scan())
    write_xyz_ply(geom, arkit_capture(T_TRUE, center=(3.0, 1.5)))
    with TestClient(app) as c:
        r = c.post("/scenes", files={"cloud": ("scan.ply", scan.read_bytes())},
                   data={"name": "Testwohnung", "metadata": json.dumps({"up_axis": "z", "context": {"floor": 2}})})
        assert r.status_code == 202, r.text
        scene = c.get(f"/scenes/{r.json()['id']}").json()
        assert scene["status"] == "ready", scene["error"]
        yield c, scene, geom.read_bytes()


def upload(c, scene_id, geom: bytes | None, session: str | None):
    files = {"image": ("p.jpg", jpeg(), "image/jpeg")}
    if geom is not None:
        files["geometry"] = ("geometry.ply", geom)
    meta = {"arkit": ARKIT_CAM, "scene_id": scene_id, "arkit_session_id": session,
            "scene_hint": {"center": [3.5, 1.0, 1.3], "radius": 2.5}}
    r = c.post("/captures", files=files, data={"metadata": json.dumps(meta)})
    assert r.status_code == 202, r.text
    return c.get(f"/captures/{r.json()['id']}").json()


def check_damage(cap):
    assert cap["status"] == "completed", (cap["status"], cap["error"])
    d = [x for x in cap["damages_3d"] if x["frame"] == "scene"]
    assert len(d) == 1
    assert np.allclose(d[0]["centroid"], EXPECTED, atol=0.05), d[0]["centroid"]
    assert np.allclose(d[0]["normal"], [0, 1, 0], atol=0.05)
    assert d[0]["area_est"] == pytest.approx(0.48, rel=0.1)   # 40x30 px at 2 m, f = 100 px
    return d[0]


def test_scene_preprocessing(client_and_scene):
    _, scene, _ = client_and_scene
    st = scene["stats"]
    assert st["n_points_proj"] <= st["n_points"] and scene["preview_url"].endswith("preview.ply")
    assert np.allclose(st["bounds_min"], [0, 0, 0], atol=0.05)


def test_automatic_alignment_and_localization(client_and_scene):
    c, scene, geom = client_and_scene
    cap = upload(c, scene["id"], geom, session="session-1")
    reg = cap["registration"]
    assert reg["ok"] and reg["method"] in ("global", "yaw_sweep"), reg
    assert np.linalg.norm(np.asarray(reg["matrix"])[:3, 3] - T_TRUE[:3, 3]) < 0.03
    assert np.allclose(np.asarray(reg["photo_camera_scene"]["matrix"])[:3, 3], C2W_SCENE[:3, 3], atol=0.05)
    d = check_damage(cap)
    pts = c.get(d["points_url"].replace("http://testserver", "")).json()
    assert len(pts) > 0 and np.abs(np.asarray(pts)[:, 1]).max() < 0.05   # on the wall y=0


def test_same_session_reuses_alignment(client_and_scene):
    c, scene, _ = client_and_scene
    cap = upload(c, scene["id"], None, session="session-1")
    assert cap["registration"]["method"] == "session"
    check_damage(cap)


def test_manual_alignment_fallback(client_and_scene):
    c, scene, _ = client_and_scene
    cap = upload(c, scene["id"], None, session=None)
    assert cap["status"] == "needs_alignment"
    r = c.post(f"/captures/{cap['id']}/registration", json={"matrix": T_TRUE.tolist()})
    assert r.status_code == 202
    cap = c.get(f"/captures/{cap['id']}").json()
    assert cap["registration"]["method"] == "manual"
    check_damage(cap)

    dmg = c.get(f"/scenes/{scene['id']}/damages").json()
    assert len(dmg) >= 3 and all(x["frame"] == "scene" for x in dmg)
    assert dmg[0]["image_url"] and dmg[0]["label"] == "Riss"


def test_unknown_scene_rejected(client_and_scene):
    c, _, _ = client_and_scene
    r = c.post("/captures", files={"image": ("p.jpg", jpeg(), "image/jpeg")},
               data={"metadata": json.dumps({"scene_id": "nope"})})
    assert r.status_code == 404
