"""GET /viewer/sites[/id] matches the web-viewer prototype's MockApi shape."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from inspection.detection import set_detector
from inspection.detection.fake import FakeDetector

from .conftest import write_3dgs_ply
from .synthetic_room import Room, look

K = {"fx": 380.0, "fy": 380.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}
PHOTO = look((1.5, 1.2, 1.4), 270, 0)      # map: z up, looking along -y at the wall y=0


def quat_to_R(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    room = Room(seed=4)
    ply = tmp_path_factory.mktemp("v") / "splat.ply"
    write_3dgs_ply(ply, room.surface_points())
    meta = {"property": "Property A", "floor_label": "3rd floor", "context": {"category": "Apartment"},
            "rooms": [{"name": "Bath", "polygon": [[0, 0], [3, 0], [3, 4], [0, 4]]}]}
    with TestClient(app) as c:
        c.post("/maps", data={"name": "Unit 3.04", "map_id": "viewer-unit", "metadata": json.dumps(meta)},
               files={"splat": ("splat.ply", ply.read_bytes())})
        for cls, pose in (("crack", PHOTO), ("stain", None)):
            set_detector(FakeDetector(damage_class=cls))
            md = {"map_id": "viewer-unit", "intrinsics": K, "source": {"kind": "phone", "device_id": "iphone-1"},
                  "recorder": "M. Keller", "pose_convention": "opencv"}
            if pose is not None:
                md["camera_to_map"] = pose.tolist()
            r = c.post("/inspect", files={"image": ("p.jpg", room.render(PHOTO, K), "image/jpeg")},
                       data={"metadata": json.dumps(md)})
            assert r.status_code == 200, r.text
        iid = c.get("/maps/viewer-unit/issues").json()[0]["issue_id"]
        c.patch(f"/issues/{iid}", json={"status": "repaired", "by": "M. Keller"})
        sites = c.get("/viewer/sites").json()
        yield sites, c.get("/viewer/sites/viewer-unit").json()


def test_site_shape(site):
    sites, s = site
    assert {"id": "viewer-unit", "option": "Unit 3.04 · Apartment"} in sites
    for key in ("id", "name", "option", "kind", "category", "location", "scanned_at", "map_id", "camera",
                "summary", "monthly", "issues", "captures", "splat_url", "splat_to_viewer", "types"):
        assert key in s, key
    assert s["location"] == "Property A, 3rd floor" and len(s["monthly"]) == 12
    assert set(s["summary"]) >= {"repaired_this_year", "avg_days_to_repair", "observations_total"}
    assert s["types"]["leak"]["label"] == "Stain" and s["types"]["crack"]["metric"] == "length"
    assert set(s["camera"]) == {"dir", "center", "box"}


def test_floor_map(site):
    """The dashboard's floor map: built with the map, same pixel convention as the floor plan, issue on it."""
    _, s = site
    fm = s["floor_map"]
    assert s["floor_map_url"].endswith("/floor_map.png") and "floor_map" not in s["floor_plan"]
    assert fm["width"] > 0 and fm["area_m2"] > 5 and fm["wall_m2"] > 0
    x, y = s["issues"][0]["floor_xy"]
    px, py = (x - fm["origin_x"]) / fm["m_per_px"], (fm["origin_y"] - y) / fm["m_per_px"]
    assert 0 <= px < fm["width"] and 0 <= py < fm["height"]


def test_issue_in_viewer_frame(site):
    _, s = site
    iss = s["issues"][0]
    assert iss["type"] == "crack" and iss["where"] == "Bath" and iss["repaired_by"] == "M. Keller"
    assert iss["repaired_at"] is not None
    assert np.allclose(iss["position_m"], [1.5, 1.4, 0.0], atol=0.05)     # map (x, y, z) -> viewer (x, z, -y)
    assert np.allclose(iss["normal"], [0, 0, -1], atol=0.05)              # towards the camera: map +y -> viewer -z
    o = iss["observations"][0]
    assert o["source"] == {"kind": "phone", "device_id": "iphone-1"} and o["capture_id"]
    assert np.allclose(o["camera_pose"]["t"], [1.5, 1.4, -1.2], atol=1e-3)
    R = quat_to_R(o["camera_pose"]["q"])
    forward = -R[:, 2]                                                    # three.js cameras look along -z
    assert np.allclose(forward, [0, 0, 1], atol=1e-3)                     # map -y = viewer +z
    assert np.allclose(R[:, 1], [0, 1, 0], atol=1e-3)                     # camera up = viewer up


def test_captures_pipeline_stage(site):
    _, s = site
    by_status = {c["status"]: c for c in s["captures"]}
    assert by_status["done"]["stage"] == 5 and "placed" in by_status["done"]["result"]
    failed = by_status["failed"]
    assert failed["stage"] == 1 and failed["reason"]                      # stopped at "Localized (VPS)"
    assert by_status["done"]["size_mb"] is not None


def test_backend_serves_the_viewer_page(site):
    """The backend hosts viewer/index.html (live mode) next to the /viewer API."""
    with TestClient(app) as c:
        home = c.get("/", follow_redirects=False)
        assert home.status_code in (302, 307) and home.headers["location"] == "/viewer/"
        page = c.get("/viewer/")
        assert page.status_code == 200 and "LiveApi" in page.text
        assert c.get("/design/system/tokens.css").status_code == 200
        assert isinstance(c.get("/viewer/sites").json(), list)        # the API is not shadowed


def test_captures_carry_vps_result(site):
    """Every capture with a localization shows up in the VPS layer, also without damage."""
    _, s = site
    by_status = {c["status"]: c for c in s["captures"]}
    v = by_status["done"]["vps"]
    assert v["method"] == "client" and v["confidence"] == 1.0 and v["threshold"] == 0.5
    assert np.allclose(v["camera_pose"]["t"], [1.5, 1.4, -1.2], atol=1e-3)          # same frame as observations
    assert v["fov_y"] and v["aspect"] == pytest.approx(640 / 480, rel=1e-3)
    failed = by_status["failed"]["vps"]
    assert failed["camera_pose"] is None and failed["error"]
