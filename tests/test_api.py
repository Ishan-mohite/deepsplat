"""End to end: map upload, POST /inspect (robot with known pose), /v1 app upload with VPS."""
import io
import json
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from inspection.detection import set_detector
from inspection.detection.fake import FakeDetector
from localization.cameras import GL_TO_CV, R_P
from localization.vps import VpsResult, set_vps

from .conftest import noise_jpeg, wall, write_3dgs_ply

W, H = 320, 240
K = {"fx": 200.0, "fy": 200.0, "cx": 160.0, "cy": 120.0, "width": W, "height": H}


def rel(url: str) -> str:
    return url.replace("http://testserver", "")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    ply = tmp_path_factory.mktemp("map") / "splat.ply"
    write_3dgs_ply(ply, wall(3.0))
    with TestClient(app) as c:
        r = c.post("/maps", files={"splat": ("splat.ply", ply.read_bytes())},
                   data={"name": "Test Hall", "map_id": "test-hall"})
        assert r.status_code == 202, r.text
        m = c.get("/maps/test-hall").json()
        assert m["status"] == "ready", m["error"]
        assert m["stats"]["n_used"] == len(wall(3.0))
        yield c


def robot_inspect(c, box=(0.4, 0.4, 0.6, 0.6), damage=True):
    set_detector(FakeDetector(damage=damage, box=box))
    meta = {"map_id": "test-hall", "intrinsics": K, "camera_to_map": np.eye(4).tolist(),
            "pose_convention": "opencv", "source": {"kind": "robot", "device_id": "patrol-02"}}
    r = c.post("/inspect", files={"image": ("f.jpg", noise_jpeg(W, H), "image/jpeg")},
               data={"metadata": json.dumps(meta)})
    assert r.status_code == 200, r.text
    return r.json()


def test_inspect_new_worsening_and_repair_suggestion(client):
    c = client
    # 1st inspection: 64 x 48 px crack at 3 m -> ~0.96 m long -> new issue, severity high
    cap = robot_inspect(c)
    assert cap["status"] == "done", cap["error"]
    assert cap["localization"]["method"] == "client"
    obs = cap["observations"][0]
    assert obs["change"] == "new" and obs["source"] == {"kind": "robot", "device_id": "patrol-02"}
    assert np.allclose(obs["position_m"], [0, 0, 3], atol=0.03)
    assert obs["length_m"] == pytest.approx(0.96, rel=0.08)
    iid = obs["issue_id"]
    issue = c.get(f"/issues/{iid}").json()
    assert issue["status"] == "new" and issue["severity"] == "high" and issue["type"] == "crack"
    assert c.get(rel(obs["mask_url"])).status_code == 200

    # 2nd: same spot, crack ~1.9 m -> same issue, worsening, critical
    cap = robot_inspect(c, box=(0.2, 0.4, 0.8, 0.6))
    assert cap["observations"][0]["issue_id"] == iid and cap["observations"][0]["change"] == "worsening"
    issue = c.get(f"/issues/{iid}").json()
    assert issue["status"] == "worsening" and issue["severity"] == "critical" and len(issue["observations"]) == 2

    # 3rd: confident view of the spot, nothing found -> repaired suggested (not closed)
    cap = robot_inspect(c, damage=False)
    assert cap["status"] == "done" and cap["observations"] == []
    issue = c.get(f"/issues/{iid}").json()
    assert issue["repair_suggested"] and issue["status"] == "worsening"

    # viewer + report + human confirmation
    pins = c.get("/maps/test-hall/issues").json()
    assert [p["issue_id"] for p in pins] == [iid]
    first_only = c.get("/maps/test-hall/issues", params={"as_of": issue["observations"][0]["timestamp"]}).json()
    assert first_only[0]["status"] == "new" and len(first_only[0]["observations"]) == 1
    assert iid in c.get("/maps/test-hall/report.csv").text
    r = c.patch(f"/issues/{iid}", json={"status": "repaired"})
    assert r.json()["status"] == "repaired" and not r.json()["repair_suggested"]
    assert c.get("/maps/test-hall/issues").json() == []


def test_review_rejects_false_positive(client):
    c = client
    cap = robot_inspect(c, box=(0.05, 0.05, 0.15, 0.15))      # a new crack elsewhere
    obs = cap["observations"][0]
    r = c.patch(f"/observations/{obs['obs_id']}", json={"review": "rejected", "reviewed_by": "inspector-1"})
    assert r.status_code == 200 and r.json()["review"] == "rejected"
    assert c.get(f"/issues/{obs['issue_id']}").json()["status"] == "rejected"


# ---------------------------------------------------------------- iOS app (/v1, portrait raw frames)

T_DEV_TO_MAP = np.array([[0, -1, 0, 1.5], [1, 0, 0, -0.5], [0, 0, 1, 0.2], [0, 0, 0, 1]], dtype=float)


class FakeVPS:
    """Localizes every frame exactly: map pose = T · device pose."""
    name = "fake"

    def has_index(self, map_id):
        return True

    def localize(self, map_id, frames):
        return [VpsResult(c2w=T_DEV_TO_MAP @ f.c2w, num_inliers=200, confidence=1.0) for f in frames]


def raw_arkit_pose(c2w_map_upright: np.ndarray) -> list:
    """ARKit camera->world (row-major) that the app would store for a portrait capture whose upright
    camera has the given map pose."""
    dev_up = np.linalg.inv(T_DEV_TO_MAP) @ c2w_map_upright
    return (dev_up @ np.linalg.inv(R_P) @ GL_TO_CV).ravel().tolist()


def capture_zip(capture_id: str) -> bytes:
    rec = lambda f, t, pose: {"file": f, "t": t, "camera_to_world": pose, "intrinsics": K,
                              "interface_orientation": "portrait", "tracking_state": "normal"}
    frames = []
    for i in range(5):
        C = np.eye(4)
        C[:3, 3] = [0.15 * i - 0.3, 0.0, -0.5]          # walking sideways, 0.5 m back
        frames.append(rec(f"frames/{i + 1:06d}.jpg", 100.0 + 0.6 * i, raw_arkit_pose(C)))
    manifest = {"schema_version": 1, "capture_id": capture_id, "map_id": "test-hall", "device": "iPhone15,2",
                "app_version": "0.1.0 (1)", "created_unix": 1794651300.0, "uptime_to_unix_offset": 1794651200.0,
                "video": {"fps_target": 5, "frames": frames},
                "photo": {**rec("photo.jpg", 103.5, raw_arkit_pose(np.eye(4))), "high_res": True}, "geo": None}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("capture.json", json.dumps(manifest))
        for i, f in enumerate(frames):
            z.writestr(f["file"], noise_jpeg(W, H, seed=i))
        z.writestr("photo.jpg", noise_jpeg(W, H, seed=99))
    return buf.getvalue()


def upload(c, capture_id):
    r = c.post("/v1/captures", json={"capture_id": capture_id, "map_id": "test-hall", "size_bytes": 1})
    assert r.status_code == 200, r.text
    assert c.put(rel(r.json()["upload_url"]), content=capture_zip(capture_id)).status_code == 200
    assert c.post(f"/v1/captures/{capture_id}/complete").json() == {"status": "processing"}
    return c.get(f"/v1/captures/{capture_id}").json()


def test_v1_upload_localized_through_frames(client):
    c = client
    set_vps(FakeVPS())
    set_detector(FakeDetector(damage_class="corrosion"))
    st = upload(c, "6F1C2A9E-0000-4000-8000-000000000001")
    assert st["status"] == "done" and st["detail"] == "done", st
    cap = c.get("/captures/6F1C2A9E-0000-4000-8000-000000000001").json()
    assert cap["image_size"] == [H, W]                        # upright portrait
    assert cap["localization"]["method"] == "vps_frames"
    assert cap["localization"]["details"]["alignment"]["n_inliers"] >= 2
    assert np.allclose(cap["localization"]["camera_pose"]["t"], [0, 0, 0], atol=1e-4)
    obs = cap["observations"][0]
    assert obs["issue_id"].startswith("ISS-") and np.allclose(obs["position_m"], [0, 0, 3], atol=0.03)
    assert cap["captured_at"].startswith("2026-11-14")


def test_v1_without_vps_then_external_frame_poses(client):
    c = client
    set_detector(FakeDetector(damage_class="stain"))
    cid = "6F1C2A9E-0000-4000-8000-000000000002"
    st = upload(c, cid)
    assert st["status"] == "done" and st["detail"] == "needs_localization"
    cap = c.get(f"/captures/{cid}").json()
    assert cap["detections"] and not cap["observations"] and len(cap["frames"]) >= 3

    # an external VPS localizes the served frames and posts their map poses
    poses = [{"frame": f["name"], "camera_to_map": (T_DEV_TO_MAP @ np.asarray(f["camera_to_world"])).tolist()}
             for f in cap["frames"]]
    r = c.post(f"/captures/{cid}/registration", json={"frame_poses": poses})
    assert r.status_code == 202, r.text
    cap = c.get(f"/captures/{cid}").json()
    assert cap["status"] == "done" and cap["localization"]["method"] == "frame_poses"
    assert np.allclose(cap["observations"][0]["position_m"], [0, 0, 3], atol=0.03)


def test_validation(client):
    c = client
    assert c.post("/v1/captures/nope/complete").status_code == 404
    meta = {"map_id": "test-hall", "intrinsics": {**K, "width": 640}}
    r = c.post("/inspect", files={"image": ("f.jpg", noise_jpeg(W, H), "image/jpeg")}, data={"metadata": json.dumps(meta)})
    assert r.status_code == 422
    assert c.post("/localize", files={"image": ("f.jpg", noise_jpeg(W, H), "image/jpeg")},
                  data={"metadata": json.dumps({"map_id": "test-hall", "intrinsics": K})}).status_code == 503


def test_delete_capture(client):
    """DELETE /captures/{id} removes the row, its detections and every stored file; captures that
    filed records stay (hash chain)."""
    from backend.storage import storage

    c = client
    cap = robot_inspect(c, damage=False)
    assert cap["status"] == "done" and not cap["observations"]
    files = storage.list_keys(f"captures/{cap['id']}/")
    assert files and rel(cap["image_url"]).removeprefix("/files/") in files
    r = c.delete(f"/captures/{cap['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["files"] == len(files)
    assert storage.list_keys(f"captures/{cap['id']}/") == []
    assert c.get(f"/captures/{cap['id']}").status_code == 404
    assert c.delete(f"/captures/{cap['id']}").status_code == 404

    with_records = robot_inspect(c, box=(0.1, 0.1, 0.2, 0.2))
    assert with_records["observations"]
    r = c.delete(f"/captures/{with_records['id']}")
    assert r.status_code == 409 and "ISS-" in r.json()["detail"]
    assert c.get(f"/captures/{with_records['id']}").status_code == 200
