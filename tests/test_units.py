"""imoscan UX: places (room / surface / height), evidence hash chain, floor plan."""
import io

import numpy as np
from PIL import Image

from inspection import evidence, floorplan
from inspection.placement import place, surface

ROOMS = [{"name": "Bath", "polygon": [[0, 0], [3, 0], [3, 4], [0, 4]]},
         {"name": "Living", "polygon": [[3, 0], [6, 0], [6, 4], [3, 4]]}]


def test_surface_compass():
    # normal points into the room: a wall whose normal points south (-y) is the north wall
    assert surface([0, -1, 0]) == "north wall"
    assert surface([0, 1, 0]) == "south wall"
    assert surface([1, 0, 0]) == "west wall"
    assert surface([-1, 0, 0]) == "east wall"
    assert surface([0, 0, 1]) == "floor" and surface([0, 0, -1]) == "ceiling"
    assert surface([0, -1, 0], north=[1, 0]) == "west wall"      # north along +x
    assert surface([0, 0, -1], up_axis="y") == "south wall"       # y-up map: floor plane is (x, -z)


def test_place():
    pl = place([1.5, 0.0, 1.4], [0, 1, 0], floor_z=0.0, rooms=ROOMS)
    assert pl == {"room": "Bath", "surface": "south wall", "height_m": 1.4, "floor_xy": [1.5, 0.0]}
    assert place([4.0, 2.0, 0.0], [0, 0, 1], floor_z=0.0, rooms=ROOMS)["room"] == "Living"
    assert place([9.0, 2.0, 1.0], [0, 0, 1], floor_z=0.0, rooms=ROOMS)["room"] is None


def test_hash_chain_detects_tampering():
    recs, prev = [], evidence.GENESIS
    for i in range(3):
        f = {"obs": i, "type": "crack", "image_sha256": evidence.sha256(bytes([i]))}
        h = evidence.record_hash(f, prev)
        recs.append((f, prev, h))
        prev = h
    assert evidence.verify_chain(recs)["ok"]
    recs[1][0]["type"] = "scratch"                       # edit a stored record
    assert evidence.verify_chain(recs) == {"ok": False, "n_records": 3, "broken_at": 1}
    assert not evidence.verify_chain([recs[0], recs[2]])["ok"]   # delete a record


def test_floor_plan_render():
    xs, ys = np.meshgrid(np.arange(0, 6, 0.02), np.arange(0, 4, 0.02))
    floor = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], 1)
    wall = np.stack([np.full(300, 3.0), np.linspace(0, 4, 300), np.full(300, 1.0)], 1)
    xyz = np.vstack([floor, wall])
    rgb = np.vstack([np.full((len(floor), 3), 0.8), np.zeros((len(wall), 3))])
    png, meta = floorplan.render(xyz, rgb, floor_z=0.0)
    img = np.asarray(Image.open(io.BytesIO(png)))
    assert img.shape[1] == meta["width"] and abs(meta["width"] * 0.02 - 6.6) < 0.05
    u, v = floorplan.to_px([3.0, 2.0], meta)
    assert img[int(v), int(u), :3].sum() < 100        # the wall is drawn dark, seen from above
    assert img[int(v), int(u) + 20, :3].sum() > 500   # floor next to it is light
    assert img.shape[2] == 4 and img[0, 0, 3] == 0 and img[int(v), int(u), 3] == 255   # margin transparent
    assert tuple(np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))[0, 0]) == (245, 245, 245)  # RGB readers


def test_viewer_splat_copy(tmp_path):
    from inspection.splatfile import VIEWER_FIELDS, read_vertices, viewer_copy

    from .conftest import write_3dgs_ply
    xyz = np.random.default_rng(0).normal(size=(1000, 3))
    logit = np.full(1000, 3.0)
    logit[:100] = -8.0                                     # floaters
    write_3dgs_ply(tmp_path / "s.ply", xyz, logit)
    st = viewer_copy(tmp_path / "s.ply", tmp_path / "v.ply")
    v = read_vertices(tmp_path / "v.ply")
    assert list(v.dtype.names) == VIEWER_FIELDS and st["n_out"] == 900 == len(v)
    assert "nx" in st["dropped_fields"]
    assert np.allclose(v["x"], xyz[100:, 0], atol=1e-6)
