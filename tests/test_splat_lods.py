"""Splat detail levels for the browser."""
import json

import numpy as np
from fastapi.testclient import TestClient

from backend import pipeline
from backend.db import engine
from backend.main import app
from backend.models import Map
from inspection.splatfile import VIEWER_FIELDS, read_vertices, thin, viewer_copy, write_vertices
from sqlmodel import Session

from .conftest import wall, write_3dgs_ply


def _viewer_array(n, seed=0):
    rng = np.random.default_rng(seed)
    v = np.zeros(n, dtype=[(k, "<f4") for k in VIEWER_FIELDS])
    v["x"], v["y"], v["z"] = rng.normal(size=(3, n))
    v["opacity"] = rng.normal(0, 2, n)
    for k in ("scale_0", "scale_1", "scale_2"):
        v[k] = rng.normal(-4, 1, n)
    v["rot_0"] = 1
    return v


def test_thin_keeps_count_fields_and_prefers_important_gaussians():
    v = _viewer_array(20000)
    t = thin(v, 2000)
    assert len(t) == 2000 and t.dtype.names == v.dtype.names
    # opaque gaussians are preferred, the largest 1 % (floaters, streaks) are left out
    assert t["opacity"].mean() > v["opacity"].mean() + 1.0
    smax_in = np.max([v[k] for k in ("scale_0", "scale_1", "scale_2")], axis=0)
    smax_out = np.max([t[k] for k in ("scale_0", "scale_1", "scale_2")], axis=0)
    assert smax_out.max() <= np.percentile(smax_in, 99) + 0.15 * np.log(10) + 1e-5
    # slight growth to close gaps, capped at the 95th percentile of the input scales
    assert t["scale_0"].mean() > v["scale_0"].mean()
    assert len(np.unique(t[["x", "y", "z"]])) == 2000          # no duplicates
    assert len(thin(v, 50000)) == len(v)                       # nothing to thin


def test_viewer_site_lists_lods_and_old_maps_get_them(tmp_path, monkeypatch):
    import inspection.splatfile as sf
    monkeypatch.setattr(sf, "LOD_TARGETS", {"medium": 30000, "low": 10000, "minimal": 3000})
    ply = tmp_path / "s.ply"
    write_3dgs_ply(ply, wall(3.0))                             # 301 x 201 = 60501 gaussians
    with TestClient(app) as c:
        c.post("/maps", files={"splat": ("s.ply", ply.read_bytes())}, data={"name": "Lod", "map_id": "lod-map"})
        lods = c.get("/viewer/sites/lod-map").json()["splat_lods"]
        assert [l["name"] for l in lods] == ["high", "medium", "low", "minimal"]
        assert [l["gaussians"] for l in lods] == [60501, 30000, 10000, 3000]
        for l in lods:
            assert c.get(l["url"].replace("http://testserver", "")).status_code == 200

        # a map processed before detail levels existed
        with Session(engine) as s:
            m = s.get(Map, "lod-map")
            m.stats = {k: v for k, v in m.stats.items() if k != "viewer_lods"}
            s.add(m)
            s.commit()
        assert c.get("/viewer/sites/lod-map").json()["splat_lods"] == []
        assert pipeline.ensure_viewer_lods() >= 1
        assert len(c.get("/viewer/sites/lod-map").json()["splat_lods"]) == 4
