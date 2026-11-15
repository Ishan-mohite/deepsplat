"""Visual positioning: image -> 6-DoF pose in the map's (splat's) coordinate frame.

Implementations:
* `sift_vps.SiftVPS` (default): free, CPU only. SIFT on the splat's training views, keypoint depth
  from the splat, direct 2D-3D matching + PnP.
* `hloc_vps.HlocVPS` (README: NetVLAD retrieval + SuperPoint/LightGlue + PnP). More robust to
  lighting / low texture, needs torch (localization/requirements-vps.txt) and ideally a GPU.
* tests inject a fake through `set_vps`.
A hosted VPS (e.g. MultiSet) would implement the same two methods.
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .cameras import Frame


@dataclass
class VpsResult:
    c2w: np.ndarray        # camera->world in the map frame, OpenCV axes
    num_inliers: int
    confidence: float      # 0..1


class VPS(Protocol):
    name: str

    def has_index(self, map_id: str) -> bool: ...

    def build_index(self, map_id: str, views_dir, views_to_map=None, map_xyz=None) -> dict: ...

    def localize(self, map_id: str, frames: list[Frame]) -> list[VpsResult | None]: ...


class IndexSync(Protocol):
    """Persists index folders outside the container (Cloud Run disks are ephemeral)."""

    def push(self, map_id: str, local_dir) -> None: ...

    def pull(self, map_id: str, local_dir) -> bool: ...


class SyncedVPS:
    """Wraps a VPS so its per-map index folder is pushed after building and pulled on demand."""

    def __init__(self, inner, sync: IndexSync):
        self.inner, self.sync = inner, sync
        self.name = inner.name

    def has_index(self, map_id: str) -> bool:
        if self.inner.has_index(map_id):
            return True
        return self.sync.pull(map_id, self.inner._dir(map_id)) and self.inner.has_index(map_id)

    def build_index(self, map_id: str, views_dir, views_to_map=None, map_xyz=None) -> dict:
        info = self.inner.build_index(map_id, views_dir, views_to_map=views_to_map, map_xyz=map_xyz)
        self.sync.push(map_id, self.inner._dir(map_id))
        return info

    def localize(self, map_id: str, frames: list[Frame]) -> list[VpsResult | None]:
        if not self.has_index(map_id):
            raise RuntimeError(f"no VPS index for map {map_id}")
        return self.inner.localize(map_id, frames)


_override: VPS | None = None
_default: VPS | None = None


def set_vps(vps: VPS | None) -> None:
    global _override
    _override = vps


def configure(kind: str, index_root, sync: IndexSync | None = None) -> None:
    """Called by the backend at startup (VPS setting). `sync` persists indexes (e.g. to GCS)."""
    global _default
    if kind in ("", "none"):
        _default = None
    elif kind == "sift":
        from .sift_vps import SiftVPS
        _default = SiftVPS(index_root)
    elif kind == "hloc":
        from .hloc_vps import HlocVPS
        _default = HlocVPS(index_root)
    else:
        raise ValueError(f"unknown VPS {kind!r}")
    if _default is not None and sync is not None:
        _default = SyncedVPS(_default, sync)


def get_vps() -> VPS | None:
    return _override if _override is not None else _default


def confidence_from_inliers(num_inliers: int, full: int = 150) -> float:
    return round(min(1.0, num_inliers / full), 3)


# A single photo has nothing to cross-check it, so its score stays inlier-based (above).
# A multi-frame capture IS cross-checked: independent frames must agree with the device's own tracking
# (localization/alignment.py). Held-out benchmark on the scan (tools/eval_vps.py): every returned pose
# was correct even at 15+ inliers, while phone frames match the scan with ~40-140 inliers, so requiring
# 150 per frame rejected consistent, correct captures. The agreement is the strong signal; inliers only
# need to clear a modest floor.
MULTI_FRAME_INLIERS_FULL = 40


def multi_frame_confidence(agreement: float, inlier_counts: list[int]) -> float:
    """agreement (0..1, share of frames consistent with the consensus, 0 if < 3 agree) x inlier floor."""
    if not inlier_counts:
        return 0.0
    med = sorted(inlier_counts)[len(inlier_counts) // 2] if len(inlier_counts) % 2 else \
        (sorted(inlier_counts)[len(inlier_counts) // 2 - 1] + sorted(inlier_counts)[len(inlier_counts) // 2]) / 2
    return round(agreement * min(1.0, med / MULTI_FRAME_INLIERS_FULL), 3)
