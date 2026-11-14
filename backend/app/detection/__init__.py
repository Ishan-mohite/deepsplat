from functools import lru_cache

from ..config import settings
from .base import DAMAGE_CLASSES, DetectedDamage, DetectionResult, Detector


@lru_cache
def get_detector() -> Detector:
    if settings.detector == "fake":
        from .fake import FakeDetector
        return FakeDetector()
    if settings.detector == "roboflow_sam3":
        from .roboflow_sam3 import RoboflowSAM3Detector
        return RoboflowSAM3Detector(settings.roboflow_api_key)
    raise ValueError(f"unknown DETECTOR {settings.detector!r}")


__all__ = ["DAMAGE_CLASSES", "DetectedDamage", "DetectionResult", "Detector", "get_detector"]
