"""Change tracking (README step 5): match new findings to known issues.

Pure functions; the backend applies them to the database.
"""

from dataclasses import dataclass

import numpy as np

from .classes import DAMAGE_CLASSES

MATCH_RADIUS_M = 0.15
GROWTH_REL = 0.2          # +20 % ...
GROWTH_ABS_LENGTH_M = 0.02  # ... and at least 2 cm longer
GROWTH_ABS_AREA_M2 = 0.002  # ... or 20 cm² larger


@dataclass
class OpenIssue:
    key: int
    damage_class: str
    position: list[float]
    object_id: str | None = None
    bbox_min: list[float] | None = None     # 3D extent of the latest observation
    bbox_max: list[float] | None = None


def _dist_to_box(p: np.ndarray, lo, hi) -> float:
    if lo is None or hi is None:
        return np.inf
    lo, hi = np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)
    return float(np.linalg.norm(np.maximum(0, np.maximum(lo - p, p - hi))))


def find_match(damage_class: str, position, object_id: str | None, open_issues: list[OpenIssue],
               radius: float = MATCH_RADIUS_M, bbox_min=None, bbox_max=None) -> OpenIssue | None:
    """Closest open issue of the same type (and on the same object, if both known).

    Close means: centroids within `radius`, or one centroid within `radius` of the other
    finding's 3D extent. The second rule keeps a growing crack linked: when it doubles in length
    its centroid moves by a quarter of its length, but the old centroid stays inside the new extent.
    """
    best, best_d = None, radius
    p = np.asarray(position, dtype=np.float64)
    for iss in open_issues:
        if iss.damage_class != damage_class:
            continue
        if object_id and iss.object_id and object_id != iss.object_id:
            continue
        q = np.asarray(iss.position, dtype=np.float64)
        d = min(float(np.linalg.norm(p - q)), _dist_to_box(p, iss.bbox_min, iss.bbox_max),
                _dist_to_box(q, bbox_min, bbox_max))
        if d <= best_d:
            best, best_d = iss, d
    return best


def size_of(damage_class: str, length_m: float | None, area_m2: float | None) -> float | None:
    metric = DAMAGE_CLASSES.get(damage_class, {}).get("size_metric", "area")
    return length_m if metric == "length" else area_m2


def change(damage_class: str, previous: float | None, current: float | None) -> str:
    """'worsening' if the size grew clearly, else 'stable'."""
    if previous is None or current is None:
        return "stable"
    metric = DAMAGE_CLASSES.get(damage_class, {}).get("size_metric", "area")
    abs_min = GROWTH_ABS_LENGTH_M if metric == "length" else GROWTH_ABS_AREA_M2
    if current > previous * (1 + GROWTH_REL) and current - previous >= abs_min:
        return "worsening"
    return "stable"
