from .base import DetectionResult, make_damage


class FakeDetector:
    """Offline detector for development and tests: always reports one crack in the image center."""

    name = "fake"

    def __init__(self, damage: bool = True):
        self.damage = damage

    def detect(self, image_bytes: bytes, width: int, height: int) -> DetectionResult:
        dets = []
        if self.damage:
            x0, y0, x1, y1 = width * 0.4, height * 0.4, width * 0.6, height * 0.6
            d = make_damage("crack", "crack", 0.9, [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]], width, height)
            dets.append(d)
        return DetectionResult(detections=dets, model="fake", latency_s=0.0)
