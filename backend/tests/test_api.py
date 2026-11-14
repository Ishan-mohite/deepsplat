import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

from .conftest import plane, write_3dgs_ply

W, H = 200, 150


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), (200, 190, 180)).save(buf, format="JPEG")
    return buf.getvalue()


def test_full_flow(tmp_path):
    with TestClient(app) as c:
        meta = {"device_model": "iPhone 15 Pro", "location": {"latitude": 46.38, "longitude": 7.63},
                "context": {"building": "Test", "room": "Bad"}}
        r = c.post("/captures", files={"image": ("p.jpg", jpeg(), "image/jpeg"),
                                       "video": ("v.mp4", b"not-really-a-video", "video/mp4")},
                   data={"metadata": json.dumps(meta)})
        assert r.status_code == 202, r.text
        cid = r.json()["id"]

        cap = c.get(f"/captures/{cid}").json()
        assert cap["status"] == "damage_detected"
        assert cap["detection"]["has_damage"] is True
        assert cap["detection"]["classes"] == ["crack"]
        assert cap["detections"][0]["label"] == "Riss"
        assert cap["location"]["latitude"] == 46.38 and cap["context"]["room"] == "Bad"
        assert cap["video"]["size_bytes"] == len(b"not-really-a-video")
        assert cap["splat"]["status"] == "pending"
        assert c.get(cap["detection"]["overlay_url"].replace("http://testserver", "")).status_code == 200

        jobs = c.get("/reconstruction-jobs").json()
        job = next(j for j in jobs if j["capture_id"] == cid)
        assert job["video_url"].endswith("video.mp4") and job["image_width"] == W

        assert c.post(f"/reconstruction-jobs/{job['id']}/claim", json={"worker_id": "gpu-1"}).status_code == 200
        assert c.get(f"/captures/{cid}").json()["status"] == "reconstructing"
        assert c.post(f"/reconstruction-jobs/{job['id']}/claim", json={"worker_id": "gpu-2"}).status_code == 409

        ply = tmp_path / "scene.ply"
        write_3dgs_ply(ply, plane(5.0, 4, 3))
        camera = {"convention": "colmap", "qvec": [1, 0, 0, 0], "tvec": [0, 0, 0],
                  "fx": 100, "fy": 100, "cx": W / 2, "cy": H / 2, "width": W, "height": H}
        r = c.post(f"/reconstruction-jobs/{job['id']}/result",
                   files={"splat": ("scene.ply", ply.read_bytes(), "application/octet-stream")},
                   data={"camera": json.dumps(camera), "scene_info": json.dumps({"units": "arbitrary"})})
        assert r.status_code == 202, r.text

        cap = c.get(f"/captures/{cid}").json()
        assert cap["status"] == "completed", cap["error"]
        assert cap["splat"]["splat_url"].endswith(".ply")
        d3 = cap["damages_3d"]
        assert len(d3) == 1 and d3[0]["damage_class"] == "crack"
        assert np.allclose(d3[0]["centroid"], [0, 0, 5], atol=0.05)
        idx = c.get(d3[0]["indices_url"].replace("http://testserver", "")).json()
        assert len(idx) == d3[0]["n_points"] > 0

        listed = c.get("/captures", params={"has_damage": True, "damage_class": "crack"}).json()
        assert any(i["id"] == cid for i in listed)


def test_validation_errors():
    with TestClient(app) as c:
        r = c.post("/captures", files={"image": ("p.jpg", b"garbage", "image/jpeg")})
        assert r.status_code == 415
        r = c.post("/captures", files={"image": ("p.jpg", jpeg(), "image/jpeg")}, data={"metadata": "{bad"})
        assert r.status_code == 422
        assert c.get("/captures/doesnotexist").status_code == 404
        assert c.get("/files/../../etc/passwd").status_code in (400, 404)


def test_detect_frame():
    with TestClient(app) as c:
        r = c.post("/detect", files={"image": ("f.jpg", jpeg(), "image/jpeg")})
        assert r.status_code == 200
        body = r.json()
        assert body["has_damage"] and body["width"] == W and body["detections"][0]["damage_class"] == "crack"


def test_arkit_flow(tmp_path):
    """App sends the ARKit photo pose; splat trained in the ARKit frame -> no camera needed in result."""
    arkit = {"matrix": np.eye(4).tolist(), "fx": 100, "fy": 100, "cx": W / 2, "cy": H / 2,
             "width": W, "height": H, "tracking_state": "normal"}
    frames = {"frames": [{"t": 0.0, "transform": np.eye(4).tolist()}]}
    with TestClient(app) as c:
        r = c.post("/captures", files={"image": ("p.jpg", jpeg(), "image/jpeg"),
                                       "video": ("v.mov", b"vid", "video/quicktime"),
                                       "arkit_frames": ("frames.json", json.dumps(frames), "application/json")},
                   data={"metadata": json.dumps({"arkit": arkit})})
        assert r.status_code == 202, r.text
        cap = r.json()
        assert cap["image"]["camera"]["convention"] == "arkit"

        job = next(j for j in c.get("/reconstruction-jobs").json() if j["capture_id"] == cap["id"])
        assert job["photo_camera"]["fx"] == 100
        assert c.get(job["arkit_frames_url"].replace("http://testserver", "")).json() == frames

        ply = tmp_path / "arkit.ply"
        write_3dgs_ply(ply, plane(-5.0, 4, 3))   # ARKit camera looks down -z, wall 5 m away
        r = c.post(f"/reconstruction-jobs/{job['id']}/result",
                   files={"splat": ("arkit.ply", ply.read_bytes(), "application/octet-stream")},
                   data={"scene_info": json.dumps({"coordinate_frame": "arkit", "units": "m"})})
        assert r.status_code == 202, r.text

        cap = c.get(f"/captures/{cap['id']}").json()
        assert cap["status"] == "completed", cap["error"]
        d = cap["damages_3d"][0]
        assert np.allclose(d["centroid"], [0, 0, -5], atol=0.05)
        assert np.allclose(d["normal"], [0, 0, 1], atol=1e-3)
        # fake detection = 40x30 px box at 5 m with f=100 px -> 2 m x 1.5 m
        assert d["area_est"] == pytest.approx(3.0, rel=0.05)


def test_arkit_orientation_mismatch_rejected():
    portrait = {"matrix": np.eye(4).tolist(), "fx": 100, "fy": 100, "cx": 75, "cy": 100,
                "width": H, "height": W}
    with TestClient(app) as c:
        r = c.post("/captures", files={"image": ("p.jpg", jpeg(), "image/jpeg")},
                   data={"metadata": json.dumps({"arkit": portrait})})
        assert r.status_code == 422 and "unrotated" in r.text


def test_result_without_any_camera_rejected(tmp_path):
    with TestClient(app) as c:
        cid = c.post("/captures", files={"image": ("p.jpg", jpeg(), "image/jpeg"),
                                         "video": ("v.mp4", b"v", "video/mp4")}).json()["id"]
        job = next(j for j in c.get("/reconstruction-jobs").json() if j["capture_id"] == cid)
        ply = tmp_path / "s.ply"
        write_3dgs_ply(ply, plane(5.0, 1, 1))
        r = c.post(f"/reconstruction-jobs/{job['id']}/result", files={"splat": ("s.ply", ply.read_bytes())})
        assert r.status_code == 422
