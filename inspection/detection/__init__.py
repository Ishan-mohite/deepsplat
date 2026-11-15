import os
from functools import lru_cache

from .base import DetectedDamage, DetectionResult, Detector, DetectorUnavailable

_override: Detector | None = None


def set_detector(detector: Detector | None) -> None:
    """Replace the detector (tests, experiments)."""
    global _override
    _override = detector


@lru_cache
def _default(kind: str, api_key: str) -> Detector:
    if kind == "fake":
        from .fake import FakeDetector
        return FakeDetector()
    if kind == "roboflow_sam3":
        from .roboflow_sam3 import RoboflowSAM3Detector
        return RoboflowSAM3Detector(api_key)
    raise ValueError(f"unknown DETECTOR {kind!r}")


def get_detector() -> Detector:
    if _override is not None:
        return _override
    return _default(os.environ.get("DETECTOR", "roboflow_sam3"), os.environ.get("ROBOFLOW_API_KEY", ""))


def detector_available() -> bool:
    """False if the detector needs a network service that cannot be reached right now."""
    try:
        check = getattr(get_detector(), "available", None)
    except Exception:            # e.g. ROBOFLOW_API_KEY missing: it cannot detect either
        return False
    return True if check is None else bool(check())


__all__ = ["DetectedDamage", "DetectionResult", "Detector", "DetectorUnavailable", "detector_available",
           "get_detector", "set_detector"]
