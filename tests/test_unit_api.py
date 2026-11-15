"""The imoscan flow on the rendered room: unit with rooms -> record defect -> move-out handover ->
dashboard, live 'records in view', type correction, PDF, evidence chain."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.db import engine
from backend.main import app
from backend.models import Observation
from inspection.detection import set_detector
from inspection.detection.fake import FakeDetector

from .conftest import write_3dgs_ply
from .synthetic_room import Room, look

K = {"fx": 380.0, "fy": 380.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}
ROOMS = [{"name": "Bath", "polygon": [[0, 0], [3, 0], [3, 4], [0, 4]]},
         {"name": "Living", "polygon": [[3, 0], [6, 0], [6, 4], [3, 4]]}]
PHOTO = look((1.5, 1.2, 1.4), 270, 0)     # facing the wall y=0 in the bath, 1.2 m away


def rel(u):
    return u.replace("http://testserver", "")


@pytest.fixture(scope="module")
def unit(tmp_path_factory):
    room = Room(seed=2)
    ply = tmp_path_factory.mktemp("unit") / "splat.ply"
    write_3dgs_ply(ply, room.surface_points())
    meta = {"property": "Property A", "floor_label": "3rd floor", "scanned_at": "2026-03-14T09:00:00Z",
            "rooms": ROOMS, "north": [0, 1]}
    with TestClient(app) as c:
        r = c.post("/maps", data={"name": "Unit 3.04", "map_id": "unit-3-04", "metadata": json.dumps(meta)},
                   files={"splat": ("splat.ply", ply.read_bytes())})
        assert r.status_code == 202, r.text
        m = c.get("/maps/unit-3-04").json()
        assert m["status"] == "ready" and m["floor_z"] == pytest.approx(0.0, abs=0.02)
        assert m["floor_plan_url"] and c.get(rel(m["floor_plan_url"])).status_code == 200
        yield c, room


def record(c, room, pose=PHOTO, **extra):
    meta = {"map_id": "unit-3-04", "intrinsics": K, "camera_to_map": pose.tolist(), "pose_convention": "opencv",
            "source": {"kind": "phone", "device_id": "iphone-1"}, **extra}
    r = c.post("/inspect", files={"image": ("p.jpg", room.render(pose, K), "image/jpeg")},
               data={"metadata": json.dumps(meta)})
    assert r.status_code == 200, r.text
    return r.json()


def test_property_manager_flow(unit):
    c, room = unit
    set_detector(FakeDetector(box=(0.45, 0.3, 0.55, 0.7)))      # a vertical crack in the image center

    # move-in 2023: crack recorded
    cap = record(c, room, recorder="A. Muster", event_kind="move_in", timestamp="2023-06-01T10:00:00Z")
    assert cap["status"] == "done" and cap["localization"]["room"] == "Bath"
    assert cap["original_sha256"] and len(cap["original_sha256"]) == 64
    iid = cap["observations"][0]["issue_id"]
    issue = c.get(f"/issues/{iid}").json()
    assert issue["place"] == "Bath · south wall" and issue["height_m"] == pytest.approx(1.4, abs=0.05)
    assert issue["floor_plan_px"] is not None

    # move-out 2026 handover: same crack again, now longer, recorded by M. Keller
    h = c.post("/maps/unit-3-04/handovers", json={"kind": "move_out", "tenant": "T. Mieter", "started_by": "M. Keller"}).json()
    set_detector(FakeDetector(box=(0.45, 0.15, 0.55, 0.85)))
    cap2 = record(c, room, recorder="M. Keller", handover_id=h["id"], timestamp="2026-03-14T10:42:00Z")
    obs2 = cap2["observations"][0]
    assert obs2["issue_id"] == iid and obs2["change"] == "worsening"     # "Earlier record here · linked"
    assert obs2["event_kind"] == "move_out" and obs2["recorder"] == "M. Keller"
    assert obs2["prev_hash"] == c.get(f"/issues/{iid}").json()["observations"][0]["record_hash"]
    ho = c.post(f"/handovers/{h['id']}/finish").json()
    assert ho["status"] == "finished" and ho["issue_ids"] == [iid] and ho["n_records"] == 1

    # live camera from the doorway: the crack is in view, with a pixel for the AR marker
    view = c.post("/maps/unit-3-04/in-view", json={"camera_to_map": look((1.5, 3.0, 1.5), 270, -5).tolist(),
                                                   "intrinsics": K}).json()
    assert view["room"] == "Bath" and [r["issue_id"] for r in view["records"]] == [iid]
    assert view["records"][0]["n_records"] == 2
    away = c.post("/maps/unit-3-04/in-view", json={"camera_to_map": look((4.5, 2.0, 1.5), 90, 0).tolist(),
                                                   "intrinsics": K}).json()
    assert away["room"] == "Living" and away["records"] == []

    # the open model called it a crack, the manager corrects it; then repaired
    assert c.patch(f"/issues/{iid}", json={"type": "scratch"}).json()["type_corrected"]
    dash = c.get("/maps/unit-3-04/dashboard").json()
    assert dash["open_findings"] == 1 and dash["records"] == 2
    assert dash["latest_records"][0]["recorder"] == "M. Keller" and dash["open_list"][0]["place"] == "Bath · south wall"
    rep = c.patch(f"/issues/{iid}", json={"status": "repaired"}).json()
    assert rep["days_to_repair"] is not None
    dash = c.get("/maps/unit-3-04/dashboard").json()
    assert dash["open_findings"] == 0 and dash["findings_by_status"]["closed"] == 1

    # rooms renamed on the floor plan -> places follow
    c.patch("/maps/unit-3-04", json={"rooms": [{**ROOMS[0], "name": "Bathroom"}, ROOMS[1]]})
    assert c.get(f"/issues/{iid}").json()["place"] == "Bathroom · south wall"

    # PDF report (unit and handover)
    pdf = c.get("/maps/unit-3-04/report.pdf", params={"include_closed": True})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert c.get("/maps/unit-3-04/report.pdf", params={"handover_id": h["id"]}).content[:4] == b"%PDF"

    # evidence: chain intact, then a stored record is altered -> detected
    assert c.get("/maps/unit-3-04/verify").json()["ok"]
    with Session(engine) as s:
        o = s.exec(select(Observation).where(Observation.capture_id == cap["id"])).first()
        o.length_m = 0.01
        s.add(o)
        s.commit()
    v = c.get("/maps/unit-3-04/verify").json()
    assert v["ok"] is False and v["broken_at"] == 0
