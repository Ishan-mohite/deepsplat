"""Figures that show the result on the splat itself."""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LightSource, ListedColormap


def _extent(g):
    return [g["x_range"][0], g["x_range"][1], g["y_range"][0], g["y_range"][1]]


def alignment_figure(path: Path, raw_xyz, aligned_xyz, al, slab_half=0.4, seed=0):
    """Side sections (x-z) of the splat before / after alignment + floor height histogram."""
    rng = np.random.default_rng(seed)
    fig, ax = plt.subplots(1, 3, figsize=(18, 5.2), gridspec_kw={"width_ratios": [1.15, 1.15, 0.7]})
    up = al.up_in_splat
    for a, pts, title in ((ax[0], raw_xyz, "BEFORE: raw splat frame"), (ax[1], aligned_xyz, "AFTER: floor-aligned (floor = 0)")):
        c = pts.mean(0)
        sel = np.abs(pts[:, 1] - c[1]) < slab_half
        q = pts[sel]
        q = q[rng.choice(len(q), min(len(q), 60000), replace=False)]
        a.scatter(q[:, 0], q[:, 2], s=0.6, c="#334155", alpha=0.5, linewidths=0)
        a.set_aspect("equal"); a.set_title(title, fontsize=12, weight="bold"); a.set_xlabel("x (m)"); a.set_ylabel("z (m)")
        a.grid(alpha=0.25)
    # floor line in the raw view: plane n.p = offset  ->  in the y-slab, z = (offset - nx*x - ny*y)/nz
    n = al.floor_normal_in_splat
    xs = np.array(ax[0].get_xlim())
    cy = raw_xyz[:, 1].mean()
    ax[0].plot(xs, (al.floor_offset - n[0] * xs - n[1] * cy) / n[2], color="#ef4444", lw=1.6, label="fitted floor plane")
    ax[0].legend(loc="upper right")
    ax[1].axhline(0, color="#ef4444", lw=1.6, label="z = 0 floor")
    ax[1].legend(loc="upper right")
    # histograms of height
    for pts, col, lab in ((raw_xyz[:, 2] - al.floor_offset / n[2], "#94a3b8", "raw z (shifted)"), (aligned_xyz[:, 2], "#0ea5e9", "aligned z")):
        h, e = np.histogram(pts, bins=np.arange(-1, 3, 0.02))
        ax[2].plot(e[:-1] + 0.01, h, color=col, label=lab, lw=1.4)
    ax[2].set_title("Height histogram near the floor", fontsize=12, weight="bold")
    ax[2].set_xlabel("height above floor (m)"); ax[2].axvline(0, color="#ef4444", lw=1); ax[2].legend(); ax[2].grid(alpha=0.25)
    fig.suptitle(f"Floor alignment · tilt removed {al.tilt_deg:.2f}° · floor plane at z={al.floor_offset:.3f} m in the raw frame "
                 f"· {al.stats['floor_inliers']:,} floor inliers", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def overview(path: Path, g: dict):
    ext = _extent(g)
    fig, ax = plt.subplots(2, 2, figsize=(15, 13))
    ax = ax.ravel()
    ax[0].imshow(g["topdown"], extent=ext)
    ax[0].set_title("Splat, top-down (colors from the gaussians)", weight="bold")

    ground = np.ma.masked_invalid(g["ground"])
    ls = LightSource(azdeg=315, altdeg=40)
    gz = np.nan_to_num(g["ground"], nan=0.0)
    rgb = ls.shade(gz, cmap=plt.cm.viridis, blend_mode="overlay", vert_exag=6, dx=g["res"], dy=g["res"])
    rgb[~g["has_ground"]] = 1.0
    im = ax[1].imshow(rgb, extent=ext)
    sm = plt.cm.ScalarMappable(cmap="viridis", norm=plt.Normalize(np.nanmin(g["ground"]), np.nanmax(g["ground"])))
    plt.colorbar(sm, ax=ax[1], fraction=0.046, label="ground height above floor (m)")
    ax[1].set_title("Elevation map (ground surface, hill-shaded)", weight="bold")

    ax[2].imshow(g["topdown"], extent=ext, alpha=0.55)
    ov = np.zeros(g["shape"] + (4,))
    ov[g["has_ground"] & g["trav"]] = [0.1, 0.8, 0.3, 0.55]
    ov[g["has_ground"] & ~g["trav"]] = [0.9, 0.1, 0.1, 0.65]
    ax[2].imshow(ov, extent=ext)
    ax[2].set_title("Traversable (green) / blocked (red) for a ground robot, over the splat", weight="bold")

    sl = np.ma.masked_invalid(g["slope"])
    m = ax[3].imshow(sl, extent=ext, cmap="magma", vmin=0, vmax=30)
    plt.colorbar(m, ax=ax[3], fraction=0.046, label="slope (deg)")
    ax[3].set_title("Ground slope", weight="bold")
    for a in ax:
        a.set_xlabel("x (m)"); a.set_ylabel("y (m)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def robot_map(path: Path, g: dict):
    """What a nav2 / odometry robot gets: free / occupied / unknown at map resolution."""
    ext = _extent(g)
    img = np.full(g["shape"] + (3,), 0.80)
    img[g["has_ground"] & g["trav"]] = 1.0
    img[g["has_ground"] & ~g["trav"]] = 0.0
    fig, a = plt.subplots(figsize=(8, 8 * g["shape"][0] / g["shape"][1]))
    a.imshow(img, extent=ext)
    a.set_title("ROS occupancy grid (free = white, blocked = black, unknown = grey)", weight="bold")
    a.set_xlabel("x (m)"); a.set_ylabel("y (m)")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def preview3d(path: Path, npz: Path, stride: int = 4, elev=38, azim=-62):
    """Static 3D picture of the ground surface (green = robot can drive, red = blocked)."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    z = np.load(npz)
    gnd, trav = z["ground"][::stride, ::stride], z["trav"][::stride, ::stride]
    res = float(z["res"]) * stride
    ox, oy = [float(v) for v in z["origin_xy"]]
    H0 = z["ground"].shape[0]
    H, W = gnd.shape
    X = ox + (np.arange(W) * stride + 0.5) * float(z["res"])
    Y = oy + (H0 - np.arange(H) * stride - 0.5) * float(z["res"])
    polys, cols = [], []
    for i in range(H - 1):
        for j in range(W - 1):
            q = gnd[i:i + 2, j:j + 2]
            if np.isnan(q).any() or np.ptp(q) > 0.6:
                continue
            polys.append([(X[j], Y[i], gnd[i, j]), (X[j + 1], Y[i], gnd[i, j + 1]),
                          (X[j + 1], Y[i + 1], gnd[i + 1, j + 1]), (X[j], Y[i + 1], gnd[i + 1, j])])
            cols.append((0.13, 0.7, 0.37) if trav[i, j] else (0.85, 0.3, 0.3))
    fig = plt.figure(figsize=(11, 8.5))
    ax = fig.add_subplot(111, projection="3d")
    ax.add_collection3d(Poly3DCollection(polys, facecolors=cols, edgecolors="none"))
    ax.set_xlim(X.min(), X.max()); ax.set_ylim(Y.min(), Y.max()); ax.set_zlim(0, 6)
    ax.set_box_aspect((X.max() - X.min(), Y.max() - Y.min(), 6))
    ax.view_init(elev=elev, azim=azim)
    ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)"); ax.set_zlabel("height above floor (m)")
    ax.set_title("3D elevation map: green = robot can drive, red = blocked (steps, seats, walls)", weight="bold")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
