"""Detector interface: image -> damage masks with type and confidence.

Implementations: Roboflow-hosted SAM 3 (default), a fake detector for tests. A self-hosted model
(YOLO-seg, SAM fine-tuned on damage data) plugs in behind the same interface.
"""

from dataclasses import dataclass, field
from typing import Protocol

FULL_FRAME_RATIO = 0.9


@dataclass
class DetectedDamage:
    damage_class: str
    prompt: str
    confidence: float
    polygons: list[list[list[float]]]
    bbox: list[float]
    area_px: float
    area_ratio: float

    @property
    def covers_full_frame(self) -> bool:
        return self.area_ratio >= FULL_FRAME_RATIO


@dataclass
class DetectionResult:
    detections: list[DetectedDamage]
    model: str
    latency_s: float
    raw: dict = field(default_factory=dict)


class Detector(Protocol):
    name: str

    def detect(self, image_bytes: bytes, width: int, height: int) -> DetectionResult: ...


def polygon_area(poly: list[list[float]]) -> float:
    if len(poly) < 3:
        return 0.0
    s = 0.0
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


def bbox_iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def dedupe(detections: list[DetectedDamage], iou: float = 0.8) -> list[DetectedDamage]:
    """Drop near-identical masks found by several prompts/classes; keep the most confident one."""
    kept: list[DetectedDamage] = []
    for d in sorted(detections, key=lambda d: d.confidence, reverse=True):
        if all(bbox_iou(d.bbox, k.bbox) < iou for k in kept):
            kept.append(d)
    return kept


def make_damage(damage_class: str, prompt: str, confidence: float,
                polygons: list[list[list[float]]], width: int, height: int) -> DetectedDamage | None:
    polygons = [p for p in polygons if len(p) >= 3]
    if not polygons:
        return None
    xs = [x for p in polygons for x, _ in p]
    ys = [y for p in polygons for _, y in p]
    area = sum(polygon_area(p) for p in polygons)
    return DetectedDamage(
        damage_class=damage_class, prompt=prompt, confidence=round(confidence, 4),
        polygons=polygons, bbox=[min(xs), min(ys), max(xs), max(ys)],
        area_px=round(area, 1), area_ratio=round(min(area / (width * height), 1.0), 5),
    )
