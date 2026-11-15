"""Synthetic tilted room: floor alignment recovers gravity, the map finds floor and a box obstacle."""
import numpy as np

from elevmap import align, grid, source


def _room(tilt_deg=6.0, seed=0):
    rng = np.random.default_rng(seed)
    n = 120_000
    floor = np.c_[rng.uniform(-4, 4, (n, 2)), rng.normal(0, 0.004, n)]
    box = np.c_[rng.uniform(1, 1.6, (n // 6, 2)), rng.uniform(0, 0.4, n // 6)]
    wall = np.c_[np.full(n // 4, 4.0), rng.uniform(-4, 4, n // 4), rng.uniform(0, 2.5, n // 4)]
    pts = np.r_[floor, box, wall]
    up_n = np.r_[np.tile([0, 0, 1.0], (n, 1)), np.tile([0, 0, 1.0], (len(box), 1)), np.tile([1.0, 0, 0], (len(wall), 1))]
    t = np.radians(tilt_deg)
    R = np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]])   # tilt about x
    pts = pts @ R.T + [3, -2, 7]                                                        # arbitrary pose
    nrm = up_n @ R.T
    sc = np.tile([0.05, 0.05, 0.002], (len(pts), 1))
    sc[len(floor) + len(box):] = [0.002, 0.05, 0.05]
    return pts, nrm, sc, R


def test_alignment_recovers_floor(monkeypatch):
    pts, nrm, sc, R = _room()
    monkeypatch.setattr(align, "normals", lambda s, q: (nrm, np.full(len(nrm), 0.04)))
    al = align.estimate(pts, sc, np.zeros((len(pts), 4)))
    p = al.apply(pts)
    floor = p[: 120_000]
    assert abs(np.median(floor[:, 2])) < 0.01 and floor[:, 2].std() < 0.02
    assert np.degrees(np.arccos(np.clip(al.up_in_splat @ (R @ [0, 0, 1]), -1, 1))) < 0.5


def test_grid_marks_box_blocked_and_floor_free():
    rng = np.random.default_rng(1)
    floor = np.c_[rng.uniform(-3, 3, (150_000, 2)), rng.normal(0, 0.003, 150_000)]
    box = np.c_[rng.uniform(1, 1.6, (6000, 2)), rng.uniform(0.05, 0.5, 6000)]
    p = np.r_[floor, box]
    n = np.tile([0, 0, 1.0], (len(p), 1))
    g = grid.build(p, n, np.full((len(p), 3), 0.5), grid.Config(res=0.05))
    H = g["shape"][0]

    def cell(x, y):
        return H - 1 - int((y - g["origin_xy"][1]) / 0.05), int((x - g["origin_xy"][0]) / 0.05)
    assert g["trav"][cell(-2, -2)]
    assert g["occupied"][cell(1.3, 1.3)] and not g["trav"][cell(1.3, 1.3)]
    assert abs(np.nanmedian(g["ground"])) < 0.02
