"""Everything on the local network must work without internet (hotspot / router with no uplink)."""
import json
import re
from pathlib import Path

import numpy as np
import pytest
import requests
from fastapi.testclient import TestClient

from backend import pipeline
from backend.main import app
from inspection.detection import DetectorUnavailable, set_detector
from inspection.detection.fake import FakeDetector

from .conftest import noise_jpeg, wall, write_3dgs_ply

ROOT = Path(__file__).resolve().parents[1]
W, H = 320, 240
K = {"fx": 200.0, "fy": 200.0, "cx": 160.0, "cy": 120.0, "width": W, "height": H}


class OfflineDetector:
    """A cloud detector without internet."""
    name = "offline"

    def available(self):
        return False

    def detect(self, *a):
        raise DetectorUnavailable("no internet")


# ---------------------------------------------------------------- viewer / generated HTML

def test_viewer_loads_nothing_from_the_internet():
    html = (ROOT / "viewer/index.html").read_text()
    loads = re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', html)
    importmap = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1))["imports"]
    external = [u for u in loads + list(importmap.values()) if re.match(r"(https?:)?//", u)]
    assert external == [], external
    for spec, path in importmap.items():
        target = ROOT / "viewer" / path.removeprefix("./")
        assert target.exists() if not path.endswith("/") else target.is_dir(), (spec, path)
    vendor = ROOT / "viewer/vendor"
    for f in ("three/build/three.core.js", "three/examples/jsm/controls/OrbitControls.js",
              "three/examples/jsm/postprocessing/Pass.js", "fonts/fonts.css"):   # imported by the files above
        assert (vendor / f).is_file(), f
    for font in re.findall(r"url\('\./([^']+)'\)", (vendor / "fonts/fonts.css").read_text()):
        assert (vendor / "fonts" / font).is_file(), font


def test_elevation_viewer_is_self_contained():
    from elevmap.mesh3d import HTML, three_scripts
    assert "https://" not in HTML and "__THREE_SCRIPTS__" in HTML
    assert "THREE.OrbitControls" in three_scripts()
    assert "<script src=" not in (ROOT / "maps/auditorium/elevation_3d.html").read_text()


# ---------------------------------------------------------------- detection without internet

def test_roboflow_reports_unavailable_instead_of_failing(monkeypatch):
    from inspection.detection import roboflow_sam3 as rf
    det = rf.RoboflowSAM3Detector("key")
    monkeypatch.setattr(rf, "reachable", lambda: False)
    assert det.available() is False
    with pytest.raises(DetectorUnavailable):
        det.detect(noise_jpeg(W, H), W, H)
    monkeypatch.setattr(rf, "reachable", lambda: True)

    def boom(*a, **k):
        raise requests.ConnectionError("Name or service not known")
    monkeypatch.setattr(rf.requests, "post", boom)
    with pytest.raises(DetectorUnavailable):
        det.detect(noise_jpeg(W, H), W, H)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    ply = tmp_path_factory.mktemp("off") / "s.ply"
    write_3dgs_ply(ply, wall(3.0))
    with TestClient(app) as c:
        assert c.post("/maps", files={"splat": ("s.ply", ply.read_bytes())},
                      data={"name": "Offline", "map_id": "offline-map"}).status_code == 202
        yield c


def test_capture_waits_for_the_detector_and_is_finished_later(client):
    c = client
    set_detector(OfflineDetector())
    assert c.get("/health").json()["detector_reachable"] is False
    meta = {"map_id": "offline-map", "intrinsics": K, "camera_to_map": np.eye(4).tolist(), "pose_convention": "opencv"}
    cap = c.post("/inspect", files={"image": ("p.jpg", noise_jpeg(W, H), "image/jpeg")},
                 data={"metadata": json.dumps(meta)}).json()
    assert cap["status"] == "detection_pending" and "no internet" in cap["error"]
    assert cap["localization"]["method"] == "client" and cap["observations"] == []   # pose kept
    assert c.get(f"/v1/captures/{cap['id']}").json()["status"] == "processing"       # app: not lost
    site = c.get("/viewer/sites/offline-map").json()
    entry = next(x for x in site["captures"] if x["capture_id"] == cap["id"])
    assert entry["status"] == "processing" and entry["stage"] == 2                   # "Segmented (SAM 3)"

    assert pipeline.retry_pending_detections() == 0          # still offline: nothing happens

    set_detector(FakeDetector())                             # internet is back
    assert pipeline.retry_pending_detections() == 1
    cap = c.get(f"/captures/{cap['id']}").json()
    assert cap["status"] == "done" and cap["observations"] and cap["observations"][0]["issue_id"].startswith("ISS-")
    assert pipeline.retry_pending_detections() == 0
