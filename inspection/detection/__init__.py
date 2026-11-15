import os
from functools import lru_cache

from .base import DetectedDamage, DetectionResult, Detector

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


__all__ = ["DetectedDamage", "DetectionResult", "Detector", "get_detector", "set_detector"]
