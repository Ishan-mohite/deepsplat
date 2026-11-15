"""python -m elevmap --splat <path|url|github:..|gs://..> --out maps/<name>"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from . import align, export, grid, render, source


def main(argv=None):
    ap = argparse.ArgumentParser(description="Floor-aligned elevation + traversability map from a Gaussian splat")
    ap.add_argument("--splat", required=True, help="local path, https URL, github:OWNER/REPO@BRANCH/path, gs://, s3://")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--res", type=float, default=0.05, help="grid resolution in m (default 0.05)")
    ap.add_argument("--min-opacity", type=float, default=0.3)
    ap.add_argument("--robot-height", type=float, default=0.7)
    ap.add_argument("--robot-radius", type=float, default=0.25)
    ap.add_argument("--max-slope", type=float, default=20.0)
    ap.add_argument("--max-step", type=float, default=0.15)
    ap.add_argument("--z-max", type=float, default=2.4, help="ignore anything higher above the floor (ceiling)")
    ap.add_argument("--yaw", choices=["auto", "none", "force"], default="none",
                    help="rotate so walls are axis-parallel (auto only if the walls are rectilinear)")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    print(f"loading {a.splat}")
    d = source.load_splat(a.splat, a.min_opacity, log=lambda s: None)
    print(f"  {len(d['xyz']):,} opaque gaussians ({time.time() - t0:.1f}s)")

    al = align.estimate(d["xyz"], d["scale"], d["quat"], yaw=a.yaw)
    print(f"floor: plane offset {al.floor_offset:.3f} m, tilt corrected {al.tilt_deg:.2f} deg, yaw {al.yaw_deg:.1f} deg")
    p = al.apply(d["xyz"])
    nrm, _ = source.normals(d["scale"], d["quat"])
    n = nrm @ al.T[:3, :3].T

    cfg = grid.Config(res=a.res, robot_height=a.robot_height, robot_radius=a.robot_radius,
                      max_slope_deg=a.max_slope, max_step_m=a.max_step, z_max=a.z_max)
    g = grid.build(p, n, d["rgb"], cfg)

    # three.js / y-up viewers: (x, y, z) -> (x, z, -y)
    to_yup = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1.0]])
    amd = {"source": a.splat,
           "splat_to_aligned_4x4": al.T.tolist(),
           "splat_to_aligned_yup_4x4 (three.js: apply to the splat mesh)": (to_yup @ al.T).tolist(),
           "floor_plane_in_splat": {"normal": al.floor_normal_in_splat.tolist(), "offset_m": al.floor_offset},
           "tilt_corrected_deg": al.tilt_deg, "yaw_deg": al.yaw_deg, "alignment_stats": al.stats,
           "robot": {"height_m": a.robot_height, "radius_m": a.robot_radius, "max_slope_deg": a.max_slope,
                     "max_step_m": a.max_step}}
    meta = export.write_arrays(a.out, g, amd)
    export.write_ros_map(a.out, "map", g)
    export.write_csv_points(a.out, g)
    render.alignment_figure(a.out / "floor_alignment.png", d["xyz"], p, al)
    render.overview(a.out / "elevation_overview.png", g)
    render.robot_map(a.out / "robot_map.png", g)

    hg = g["has_ground"]
    summary = {"cells": int(hg.size), "mapped_cells": int(hg.sum()),
               "mapped_area_m2": round(float(hg.sum()) * a.res ** 2, 1),
               "traversable_area_m2": round(float((hg & g["trav"]).sum()) * a.res ** 2, 1),
               "ground_height_range_m": [round(float(np.nanmin(g["ground"])), 2), round(float(np.nanmax(g["ground"])), 2)],
               "floor_tilt_corrected_deg": round(al.tilt_deg, 3), "seconds": round(time.time() - t0, 1)}
    (a.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
