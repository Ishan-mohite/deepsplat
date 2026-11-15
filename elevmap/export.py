"""Write the map in formats robots and humans use."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image


def write_ros_map(out: Path, name: str, g: dict, free_thresh=0.25, occ_thresh=0.65):
    """ROS map_server / nav2 pair: <name>.pgm + <name>.yaml (origin = lower-left, aligned frame)."""
    cost, trav, has = g["cost"], g["trav"], g["has_ground"]
    pgm = np.full(cost.shape, 205, np.uint8)               # unknown
    pgm[has & trav] = 254                                  # free
    pgm[has & ~trav] = 0                                   # occupied / not traversable
    Image.fromarray(pgm).save(out / f"{name}.pgm")
    H = pgm.shape[0]
    ox, oy = g["origin_xy"]
    (out / f"{name}.yaml").write_text(
        f"image: {name}.pgm\nmode: trinary\nresolution: {g['res']}\n"
        f"origin: [{ox:.4f}, {oy:.4f}, 0.0]\nnegate: 0\noccupied_thresh: {occ_thresh}\nfree_thresh: {free_thresh}\n")


def write_arrays(out: Path, g: dict, align_meta: dict):
    np.savez_compressed(out / "elevation.npz", dsm=g["dsm"].astype(np.float32), ground=g["ground"].astype(np.float32),
                        obstacle=g["obstacle"].astype(np.float32), slope=g["slope"].astype(np.float32),
                        step=g["step"].astype(np.float32), cost=g["cost"], trav=g["trav"],
                        origin_xy=g["origin_xy"], res=g["res"])
    ground = np.nan_to_num(g["ground"], nan=-1.0)
    h16 = np.clip((ground + 1.0) / 8.0 * 65535, 0, 65535).astype(np.uint16)   # -1 .. 7 m
    Image.fromarray(h16).save(out / "ground_height_16bit.png")
    H, W = g["shape"]
    meta = {"frame": "floor-aligned: z up, floor at z=0, metres, +x right / +y up in the top-down image",
            "resolution_m": g["res"], "width": W, "height": H, "origin_xy_m": [float(v) for v in g["origin_xy"]],
            "pixel_to_xy": "x = origin_x + (col + 0.5) * res ; y = origin_y + (H - row - 0.5) * res",
            "height16_encoding": "metres = value / 65535 * 8 - 1 ; 0 = unknown",
            "cost_encoding": "0 free, 1..99 inflated, 100 lethal/non-traversable, 255 unknown", **align_meta}
    (out / "map_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def write_csv_points(out: Path, g: dict, step: int = 4):
    """x,y,z_ground,obstacle,slope,traversable at every `step`-th cell (for quick plotting / ROS bags)."""
    ys, xs = np.nonzero(g["has_ground"])
    sel = (ys % step == 0) & (xs % step == 0)
    ys, xs = ys[sel], xs[sel]
    H = g["shape"][0]
    x = g["origin_xy"][0] + (xs + 0.5) * g["res"]
    y = g["origin_xy"][1] + (H - ys - 0.5) * g["res"]
    arr = np.c_[x, y, g["ground"][ys, xs], g["obstacle"][ys, xs], g["slope"][ys, xs], g["trav"][ys, xs]]
    np.savetxt(out / "ground_points.csv", arr, delimiter=",", header="x,y,ground_z,obstacle_h,slope_deg,traversable",
               fmt="%.3f", comments="")
