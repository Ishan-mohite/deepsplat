from .base import DetectionResult, make_damage


class FakeDetector:
    """Offline detector for development and tests: one crack in the image center.

    `box` = (x0, y0, x1, y1) as fractions of the image size; `damage=False` reports nothing.
    """

    name = "fake"

    def __init__(self, damage: bool = True, damage_class: str = "crack",
                 box: tuple[float, float, float, float] = (0.4, 0.4, 0.6, 0.6)):
        self.damage, self.damage_class, self.box = damage, damage_class, box

    def detect(self, image_bytes: bytes, width: int, height: int) -> DetectionResult:
        dets = []
        if self.damage:
            fx0, fy0, fx1, fy1 = self.box
            x0, y0, x1, y1 = width * fx0, height * fy0, width * fx1, height * fy1
            dets.append(make_damage(self.damage_class, self.damage_class, 0.9,
                                    [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]], width, height))
        return DetectionResult(detections=dets, model="fake", latency_s=0.0)
