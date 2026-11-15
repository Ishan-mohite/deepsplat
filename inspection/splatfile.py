"""Read / write 3DGS .ply files as numpy structured arrays (binary little endian)."""

from pathlib import Path

import numpy as np

from .lifting import PLY_TYPES

VIEWER_FIELDS = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
                 "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]


def read_vertices(path: Path) -> np.ndarray:
    with open(path, "rb") as f:
        fmt, n, props, in_vertex = None, 0, [], False
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
                    n = int(tok[2])
            elif tok[0] == "property" and in_vertex:
                props.append((tok[2], "<" + PLY_TYPES[tok[1]]))
            elif tok[0] == "end_header":
                break
        if fmt != "binary_little_endian":
            raise ValueError("only binary_little_endian PLY is supported")
        dtype = np.dtype(props)
        return np.frombuffer(f.read(dtype.itemsize * n), dtype=dtype, count=n)


def write_vertices(path: Path, arr: np.ndarray) -> None:
    names = arr.dtype.names
    header = f"ply\nformat binary_little_endian 1.0\nelement vertex {len(arr)}\n"
    header += "".join(f"property float {k}\n" for k in names) + "end_header\n"
    packed = np.empty(len(arr), dtype=[(k, "<f4") for k in names])
    for k in names:
        packed[k] = arr[k]
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(packed.tobytes())


def viewer_copy(src: Path, dst: Path, min_opacity: float = 0.05) -> dict:
    """Lighter splat for browsers: opaque gaussians only, base color only (no f_rest_* SH bands).
    Returns stats. Raises ValueError if the file has no 3DGS fields."""
    v = read_vertices(src)
    missing = [k for k in VIEWER_FIELDS if k not in v.dtype.names]
    if missing:
        raise ValueError(f"not a 3DGS splat, missing {missing}")
    op = 1 / (1 + np.exp(-v["opacity"].astype(np.float64)))
    keep = op >= min_opacity
    out = v[keep][VIEWER_FIELDS]
    write_vertices(dst, out)
    return {"n_in": int(len(v)), "n_out": int(keep.sum()), "bytes": Path(dst).stat().st_size,
            "dropped_fields": [k for k in v.dtype.names if k not in VIEWER_FIELDS]}


# Detail levels for the browser (largest first). The viewer picks one per device.
LOD_TARGETS = {"medium": 1_000_000, "low": 400_000, "minimal": 150_000}


def thin(v: np.ndarray, n: int, seed: int = 0) -> np.ndarray:
    """Keep `n` of the gaussians in `v` (VIEWER_FIELDS layout).

    Weighted sampling without replacement (Efraimidis-Spirakis keys), weight = opacity, so opaque
    gaussians that carry the look of the surface are kept preferentially. The 1 % largest gaussians
    (floaters, long streaks) are left out. The kept ones grow slightly, by (N/n)^0.15, to close the
    gaps, but never beyond the 95th percentile of the scales (growing everything by (N/n)^0.25 turned
    the 150k level of the project splat into large blurry blobs; compared visually)."""
    if n >= len(v):
        return v.copy()
    op = 1 / (1 + np.exp(-v["opacity"].astype(np.float64)))
    scales = np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=1).astype(np.float64)
    smax = scales.max(axis=1)
    w = np.where(smax <= np.percentile(smax, 99), op, 0.0) + 1e-12
    u = np.random.default_rng(seed).random(len(v))
    keys = np.log(u) / w                                     # larger is better
    keep = np.argpartition(keys, len(v) - n)[len(v) - n:]
    out = v[np.sort(keep)].copy()
    grow = 0.15 * np.log(len(v) / n)
    cap = np.percentile(scales, 95)
    for k in ("scale_0", "scale_1", "scale_2"):
        out[k] = np.minimum(out[k] + grow, np.maximum(out[k], cap))
    return out


def lod_copies(viewer_ply: Path, out_dir: Path, stem: str = "splat_viewer") -> list[dict]:
    """Write the lower detail levels next to the viewer copy. Returns [{name, path, gaussians, bytes}]
    for the levels that are actually smaller than the input."""
    v = read_vertices(viewer_ply)
    out = []
    for name, n in LOD_TARGETS.items():
        if n >= len(v) * 0.8:
            continue
        path = Path(out_dir) / f"{stem}_{name}.ply"
        write_vertices(path, thin(v, n))
        out.append({"name": name, "path": path, "gaussians": n, "bytes": path.stat().st_size})
    return out
