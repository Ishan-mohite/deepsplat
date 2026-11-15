"""Read a 3DGS .ply from wherever it is hosted, without needing the whole file on disk.

Supported locations (``--splat``):

* local path                       ``trained_splats/splat_30000.ply``
* http(s) URL                      anything that honours ``Range`` (S3/GCS/Azure/CDN/nginx)
* GitHub LFS                       ``github:OWNER/REPO@BRANCH/path/to/file.ply``
                                   (-> media.githubusercontent.com, ``GITHUB_TOKEN`` for private repos)
* Google Cloud Storage             ``gs://bucket/object`` (-> storage.googleapis.com, optional
                                   ``GCS_ACCESS_TOKEN`` = ``gcloud auth print-access-token``)
* S3                               ``s3://bucket/key`` (public / pre-signed only, via the https endpoint)

Only the vertex columns the map needs are kept (xyz, opacity, scales, rotation, base color), streamed in
blocks, so a 700 MB splat needs ~100 MB of RAM and a remote splat is never written to disk.
"""

from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Iterator

import numpy as np

SH_C0 = 0.28209479177387814
NEEDED = ["x", "y", "z", "opacity", "scale_0", "scale_1", "scale_2",
          "rot_0", "rot_1", "rot_2", "rot_3", "f_dc_0", "f_dc_1", "f_dc_2"]
PLY_TYPES = {"float": "f4", "float32": "f4", "double": "f8", "float64": "f8", "uchar": "u1", "uint8": "u1",
             "char": "i1", "short": "i2", "ushort": "u2", "int": "i4", "uint": "u4"}


def resolve(src: str) -> tuple[str, dict]:
    """Return (path-or-https-url, request headers)."""
    h: dict = {}
    if m := re.fullmatch(r"github:([^/]+)/([^@/]+)@([^/]+)/(.+)", src):
        owner, repo, ref, path = m.groups()
        if tok := os.environ.get("GITHUB_TOKEN"):
            h["Authorization"] = f"token {tok}"
        return f"https://media.githubusercontent.com/media/{owner}/{repo}/{ref}/{path}", h
    if m := re.fullmatch(r"gs://([^/]+)/(.+)", src):
        if tok := os.environ.get("GCS_ACCESS_TOKEN"):
            h["Authorization"] = f"Bearer {tok}"
        return f"https://storage.googleapis.com/{m[1]}/{m[2]}", h
    if m := re.fullmatch(r"s3://([^/]+)/(.+)", src):
        return f"https://{m[1]}.s3.amazonaws.com/{m[2]}", h
    return src, h


class _Reader:
    """Sequential/range reader over a local file or an HTTP(S) URL."""

    def __init__(self, loc: str, headers: dict):
        self.loc, self.headers = loc, headers
        self.remote = loc.startswith(("http://", "https://"))
        self._f = None if self.remote else open(loc, "rb")

    def read(self, start: int, length: int) -> bytes:
        if not self.remote:
            self._f.seek(start)
            return self._f.read(length)
        req = urllib.request.Request(self.loc, headers={**self.headers, "Range": f"bytes={start}-{start + length - 1}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if r.status == 200 and start > 0:     # server ignored Range: refuse rather than mis-parse
            raise OSError("server does not support HTTP Range requests; download the file instead")
        return data


@dataclass
class Header:
    n: int
    dtype: np.dtype
    offset: int
    comments: list[str]


def parse_header(rd: _Reader) -> Header:
    buf = rd.read(0, 1 << 16)
    end = buf.index(b"end_header\n") + len(b"end_header\n")
    names, types, n, comments, fmt, in_v = [], [], 0, [], None, False
    for line in buf[:end].decode("ascii", "replace").splitlines():
        t = line.split()
        if not t:
            continue
        if t[0] == "format":
            fmt = t[1]
        elif t[0] == "comment":
            comments.append(line[8:])
        elif t[0] == "element":
            in_v = t[1] == "vertex"
            if in_v:
                n = int(t[2])
        elif t[0] == "property" and in_v:
            types.append(PLY_TYPES[t[1]])
            names.append(t[2])
    if fmt != "binary_little_endian":
        raise ValueError("only binary_little_endian PLY is supported")
    return Header(n, np.dtype([(k, "<" + ty) for k, ty in zip(names, types)]), end, comments)


def stream_splat(src: str, block: int = 400_000, min_opacity: float = 0.3) -> Iterator[dict[str, np.ndarray]]:
    """Yield blocks of the needed columns for gaussians with sigmoid(opacity) >= min_opacity."""
    loc, headers = resolve(src)
    rd = _Reader(loc, headers)
    hd = parse_header(rd)
    missing = [k for k in NEEDED if k not in hd.dtype.names]
    if missing:
        raise ValueError(f"not a 3DGS splat, missing {missing}")
    size = hd.dtype.itemsize
    for i in range(0, hd.n, block):
        cnt = min(block, hd.n - i)
        arr = np.frombuffer(rd.read(hd.offset + i * size, cnt * size), dtype=hd.dtype, count=cnt)
        keep = 1 / (1 + np.exp(-arr["opacity"].astype(np.float64))) >= min_opacity
        if keep.any():
            yield {k: np.ascontiguousarray(arr[k][keep]) for k in NEEDED}


def load_splat(src: str, min_opacity: float = 0.3, log=print) -> dict[str, np.ndarray]:
    parts, total = [], 0
    for blk in stream_splat(src, min_opacity=min_opacity):
        parts.append(blk)
        total += len(blk["x"])
        log(f"  ... {total:,} opaque gaussians")
    out = {k: np.concatenate([p[k] for p in parts]) for k in NEEDED}
    out["xyz"] = np.stack([out["x"], out["y"], out["z"]], 1).astype(np.float64)
    out["rgb"] = np.clip(0.5 + SH_C0 * np.stack([out["f_dc_0"], out["f_dc_1"], out["f_dc_2"]], 1), 0, 1)
    out["scale"] = np.exp(np.stack([out["scale_0"], out["scale_1"], out["scale_2"]], 1).astype(np.float64))
    q = np.stack([out["rot_0"], out["rot_1"], out["rot_2"], out["rot_3"]], 1).astype(np.float64)
    out["quat"] = q / np.linalg.norm(q, axis=1, keepdims=True)           # w, x, y, z
    return out


def normals(scale: np.ndarray, quat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Surface normal of each gaussian = its shortest axis, rotated into the splat frame.
    Returns (normals, flatness) where flatness = smallest / middle scale (small = disc-like)."""
    ax = scale.argmin(1)
    w, x, y, z = quat.T
    R = np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], 1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], 1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], 1)], 1)  # (n,3,3)
    n = R[np.arange(len(ax)), :, ax]
    s = np.sort(scale, 1)
    return n, s[:, 0] / np.maximum(s[:, 1], 1e-9)
