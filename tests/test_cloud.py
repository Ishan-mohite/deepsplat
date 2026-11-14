"""Cloud Run concerns: large splat upload via signed URLs, VPS index surviving a container restart."""
import io
import shutil
import zipfile

import numpy as np
from fastapi.testclient import TestClient

from backend.main import app
from backend.storage import LocalStorage, StorageIndexSync
from localization.cameras import Frame, Intrinsics
from localization.sift_vps import SiftVPS
from localization.vps import SyncedVPS

from .conftest import wall, write_3dgs_ply
from .synthetic_room import Room, look, views_zip

K = {"fx": 380.0, "fy": 380.0, "cx": 320.0, "cy": 240.0, "width": 640, "height": 480}


def test_map_upload_via_signed_urls(tmp_path):
    ply = tmp_path / "s.ply"
    write_3dgs_ply(ply, wall(3.0))
    with TestClient(app) as c:
        urls = c.post("/maps/upload-urls", json={"map_id": "big-unit"}).json()
        assert set(urls) == {"splat", "views"}
        assert c.post("/maps/register", json={"map_id": "big-unit", "name": "Big"}).status_code == 409  # not uploaded
        r = c.put(urls["splat"]["upload_url"].replace("http://testserver", ""), content=ply.read_bytes(),
                  headers={"Content-Type": urls["splat"]["content_type"]})
        assert r.status_code == 200
        r = c.post("/maps/register", json={"map_id": "big-unit", "name": "Big Unit",
                                           "metadata": {"property": "Property B"}})
        assert r.status_code == 202, r.text
        m = c.get("/maps/big-unit").json()
        assert m["status"] == "ready" and m["property"] == "Property B" and m["vps_status"] == "none"
        assert c.put("/v1/uploads/maps/big-unit/../../etc.ply", content=b"x").status_code == 404


def test_vps_index_survives_restart(tmp_path):
    room = Room(seed=3)
    views = tmp_path / "views"
    poses = [look((x, 2.0, 1.5), yaw, 0) for x in (2.0, 4.0) for yaw in range(0, 360, 60)]
    with zipfile.ZipFile(io.BytesIO(views_zip(room, poses, K))) as z:
        z.extractall(views)
    remote = StorageIndexSync(LocalStorage(tmp_path / "bucket", "http://x"))

    first = SyncedVPS(SiftVPS(tmp_path / "instance-1"), remote)
    first.build_index("m", views, map_xyz=room.surface_points())
    shutil.rmtree(tmp_path / "instance-1")                      # container restarted, disk gone

    second = SyncedVPS(SiftVPS(tmp_path / "instance-2"), remote)
    assert second.has_index("m")                                 # pulled from the bucket
    truth = look((3.0, 2.0, 1.5), 30, 0)
    r = second.localize("m", [Frame("q", room.render(truth, K), Intrinsics(**K), None)])[0]
    assert r is not None and np.linalg.norm(r.c2w[:3, 3] - truth[:3, 3]) < 0.05
    assert not SyncedVPS(SiftVPS(tmp_path / "instance-3"), remote).has_index("other-map")
