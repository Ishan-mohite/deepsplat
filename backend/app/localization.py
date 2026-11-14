"""Project 2D damage masks into a Gaussian splat / point cloud.

Inputs from the splat team:
  * a .ply with per-vertex x, y, z (standard 3DGS export; `opacity` used if present)
  * the pose + intrinsics of the damage photo in the SAME world frame as the .ply

Method: project every gaussian center into the damage photo, keep the ones that are visible
(coarse z-buffer) and fall inside the SAM 3 mask. Their statistics give the 3D annotation.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .detection.base import polygon_area

PLY_TYPES = {
    "char": "i1", "uchar": "u1", "short": "i2", "ushort": "u2", "int": "i4", "uint": "u4",
    "float": "f4", "double": "f8", "int8": "i1", "uint8": "u1", "int16": "i2", "uint16": "u2",
    "int32": "i4", "uint32": "u4", "float32": "f4", "float64": "f8",
}


def read_ply_points(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """Return (xyz [N,3] float64, opacity [N] in 0..1 or None) from the vertex element of a PLY."""
    with open(path, "rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError("not a PLY file")
        fmt, n_vertex, props, in_vertex, elements_before = None, 0, [], False, []
        while True:
            line = f.readline()
            if not line:
                raise ValueError("unexpected end of PLY header")
            tok = line.decode("ascii", "replace").split()
            if not tok:
                continue
            if tok[0] == "format":
                fmt = tok[1]
            elif tok[0] == "element":
                in_vertex = tok[1] == "vertex"
                if in_vertex:
                    n_vertex = int(tok[2])
                elif n_vertex == 0:
                    elements_before.append(tok[1])
            elif tok[0] == "property" and in_vertex:
                if tok[1] == "list":
                    raise ValueError("list properties in vertex element are not supported")
                props.append((tok[2], PLY_TYPES[tok[1]]))
            elif tok[0] == "end_header":
                break
        if elements_before:
            raise ValueError("vertex must be the first element in the PLY")
        names = [n for n, _ in props]
        if not {"x", "y", "z"} <= set(names):
            raise ValueError("PLY vertex element has no x/y/z")

        if fmt == "ascii":
            data = np.loadtxt(f, max_rows=n_vertex, ndmin=2)
            col = {n: data[:, i] for i, n in enumerate(names)}
        elif fmt in ("binary_little_endian", "binary_big_endian"):
            endian = "<" if fmt == "binary_little_endian" else ">"
            dtype = np.dtype([(n, endian + t) for n, t in props])
            arr = np.frombuffer(f.read(dtype.itemsize * n_vertex), dtype=dtype, count=n_vertex)
            col = {n: arr[n] for n in names}
        else:
            raise ValueError(f"unsupported PLY format {fmt}")

    xyz = np.stack([col["x"], col["y"], col["z"]], axis=1).astype(np.float64)
    opacity = None
    if "opacity" in col:
        o = col["opacity"].astype(np.float64)
        # 3DGS stores opacity as a logit; plain point clouds may store 0..1
        opacity = 1 / (1 + np.exp(-o)) if (o.min() < 0 or o.max() > 1) else o
    return xyz, opacity


@dataclass
class Camera:
    """Pinhole camera, world->camera in OpenCV axes (x right, y down, z forward)."""
    R: np.ndarray
    t: np.ndarray
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t

    @classmethod
    def from_dict(cls, d: dict) -> "Camera":
        """Accepted conventions (field `convention`):

        * "colmap":     qvec [qw, qx, qy, qz] + tvec [tx, ty, tz]  (world->camera, as in images.txt)
        * "w2c_opencv": matrix 4x4 world->camera, OpenCV axes
        * "c2w_opengl": matrix 4x4 camera->world, OpenGL axes (nerfstudio / instant-ngp transforms.json)
        * "arkit":      ARFrame.camera.transform (camera->world, x right / y up / -z forward,
                        same as c2w_opengl), matrix given as 4 rows
        Intrinsics: fx, fy, cx, cy, width, height (pixels of the image the pose belongs to).
        """
        conv = d.get("convention", "colmap")
        if conv == "arkit":
            conv = "c2w_opengl"
        if conv == "colmap":
            qw, qx, qy, qz = d["qvec"]
            R = np.array([
                [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
                [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
                [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
            ])
            t = np.asarray(d["tvec"], dtype=np.float64)
        elif conv == "w2c_opencv":
            M = np.asarray(d["matrix"], dtype=np.float64)
            R, t = M[:3, :3], M[:3, 3]
        elif conv == "c2w_opengl":
            c2w = np.asarray(d["matrix"], dtype=np.float64) @ np.diag([1.0, -1.0, -1.0, 1.0])
            w2c = np.linalg.inv(c2w)
            R, t = w2c[:3, :3], w2c[:3, 3]
        else:
            raise ValueError(f"unknown camera convention {conv!r}")
        return cls(R=R, t=t, fx=float(d["fx"]), fy=float(d["fy"]), cx=float(d["cx"]),
                   cy=float(d["cy"]), width=int(d["width"]), height=int(d["height"]))


@dataclass
class Localized:
    centroid: list[float]
    bbox_min: list[float]
    bbox_max: list[float]
    normal: list[float]
    n_points: int
    mean_depth: float
    area_est: float
    indices: np.ndarray


def check_aspect(cam: Camera, image_size: tuple[int, int], tol: float = 0.02) -> None:
    """The pose must belong to the uploaded image as-is (ARKit intrinsics are in landscape sensor
    orientation, so a rotated portrait JPEG would not match)."""
    a, b = cam.width / cam.height, image_size[0] / image_size[1]
    if abs(a - b) / b > tol:
        raise ValueError(
            f"camera is {cam.width}x{cam.height} but the image is {image_size[0]}x{image_size[1]}; "
            "send the photo unrotated in the orientation the intrinsics refer to")


def visible_points(xyz: np.ndarray, cam: Camera, opacity: np.ndarray | None = None,
                   min_opacity: float = 0.2, cell_px: int | None = None, depth_tol: float = 0.05):
    """Return (indices, u, v, depth) of points visible in the camera.

    Coarse z-buffer: a point is visible if it is within `depth_tol` of the nearest depth in its
    cell or any neighbouring cell (3x3 min filter closes holes between sparse points, so walls
    don't let the points behind them "shine through").
    """
    if cell_px is None:
        cell_px = max(4, cam.width // 240)
    idx = np.arange(len(xyz))
    if opacity is not None:
        keep = opacity >= min_opacity
        idx, xyz = idx[keep], xyz[keep]
    pc = xyz @ cam.R.T + cam.t
    z = pc[:, 2]
    front = z > 1e-6
    idx, pc, z = idx[front], pc[front], z[front]
    u = cam.fx * pc[:, 0] / z + cam.cx
    v = cam.fy * pc[:, 1] / z + cam.cy
    inside = (u >= 0) & (u < cam.width) & (v >= 0) & (v < cam.height)
    idx, u, v, z = idx[inside], u[inside], v[inside], z[inside]
    if len(idx) == 0:
        return idx, u, v, z
    gw, gh = cam.width // cell_px + 1, cam.height // cell_px + 1
    cell = (v // cell_px).astype(np.int64) * gw + (u // cell_px).astype(np.int64)
    zmin = np.full(gw * gh, np.inf)
    np.minimum.at(zmin, cell, z)
    grid = np.pad(zmin.reshape(gh, gw), 1, constant_values=np.inf)
    filt = np.min([grid[1 + dy:1 + dy + gh, 1 + dx:1 + dx + gw]
                   for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0).ravel()
    vis = z <= filt[cell] * (1 + depth_tol)
    return idx[vis], u[vis], v[vis], z[vis]


def rasterize(polygons: list, width: int, height: int, sx: float = 1.0, sy: float = 1.0) -> np.ndarray:
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for poly in polygons:
        if len(poly) >= 3:
            draw.polygon([(x * sx, y * sy) for x, y in poly], fill=1)
    return np.asarray(mask, dtype=bool)


def localize(polygons: list, image_size: tuple[int, int], vis, cam: Camera,
             xyz: np.ndarray, min_points: int = 5) -> Localized | None:
    """Select visible points inside the mask. `polygons` are in pixels of `image_size` (w, h)."""
    idx, u, v, z = vis
    if len(idx) == 0:
        return None
    mask = rasterize(polygons, cam.width, cam.height,
                     cam.width / image_size[0], cam.height / image_size[1])
    sel = mask[v.astype(np.int64), u.astype(np.int64)]
    if sel.sum() < min_points:
        return None
    pts, sel_idx = xyz[idx[sel]], idx[sel]
    mean_depth = float(z[sel].mean())
    sx, sy = cam.width / image_size[0], cam.height / image_size[1]
    mask_area_px = sum(polygon_area(p) for p in polygons) * sx * sy   # exact, unlike raster count
    area_est = mask_area_px * mean_depth ** 2 / (cam.fx * cam.fy)

    centroid = np.median(pts, axis=0)
    lo, hi = np.percentile(pts, 2, axis=0), np.percentile(pts, 98, axis=0)
    # surface normal = direction of least variance, oriented towards the camera
    if len(pts) >= 3:
        _, _, vt = np.linalg.svd(pts - pts.mean(axis=0), full_matrices=False)
        normal = vt[-1]
        if np.dot(normal, cam.center - centroid) < 0:
            normal = -normal
    else:
        normal = np.zeros(3)
    r = lambda a: [round(float(x), 5) for x in a]
    return Localized(centroid=r(centroid), bbox_min=r(lo), bbox_max=r(hi), normal=r(normal),
                     n_points=int(sel.sum()), mean_depth=round(mean_depth, 5),
                     area_est=round(area_est, 6), indices=sel_idx)
