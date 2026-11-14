import base64
import time

import requests

from ..classes import enabled_classes
from .base import DetectedDamage, DetectionResult, dedupe, make_damage

ENDPOINT = "https://serverless.roboflow.com/sam3/concept_segment"


class RoboflowSAM3Detector:
    """SAM 3 concept segmentation via Roboflow serverless (~2-3 s per image)."""

    name = "roboflow/sam3"

    def __init__(self, api_key: str, classes: dict[str, dict] | None = None, timeout: float = 60):
        if not api_key:
            raise ValueError("ROBOFLOW_API_KEY is not set")
        self.api_key = api_key
        self.classes = classes if classes is not None else enabled_classes()
        self.timeout = timeout
        self.prompts = [(cls, p) for cls, cfg in self.classes.items() for p in cfg["prompts"]]
        if not self.prompts:
            raise ValueError("no enabled damage classes")
        if len(self.prompts) > 16:
            raise ValueError("Roboflow SAM 3 accepts at most 16 prompts per request")
        self.request_thresh = min(cfg["min_conf"] for cfg in self.classes.values())

    def _post(self, payload: dict) -> dict:
        last = None
        for attempt in range(3):
            r = requests.post(ENDPOINT, json=payload, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}"})
            if r.status_code == 429 or r.status_code >= 500:
                last = r
                time.sleep(2 * (attempt + 1))
                continue
            if not r.ok:
                raise RuntimeError(f"Roboflow HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"Roboflow HTTP {last.status_code} after retries: {last.text[:300]}")

    def detect(self, image_bytes: bytes, width: int, height: int) -> DetectionResult:
        payload = {
            "image": {"type": "base64", "value": base64.b64encode(image_bytes).decode()},
            "prompts": [{"type": "text", "text": p} for _, p in self.prompts],
            "output_prob_thresh": self.request_thresh,
            "format": "polygon",
        }
        t0 = time.perf_counter()
        raw = self._post(payload)
        latency = time.perf_counter() - t0

        detections: list[DetectedDamage] = []
        for pr in raw.get("prompt_results", []):
            idx = pr.get("prompt_index", pr.get("echo", {}).get("prompt_index"))
            if idx is None or idx >= len(self.prompts):
                continue
            cls, prompt = self.prompts[idx]
            min_conf = self.classes[cls]["min_conf"]
            for pred in pr.get("predictions", []):
                conf = float(pred.get("confidence", 0))
                if conf < min_conf:
                    continue
                d = make_damage(cls, prompt, conf, pred.get("masks", []), width, height)
                if d:
                    detections.append(d)
        model = raw.get("resolved_model", {}).get("model_id", "sam3")
        return DetectionResult(detections=dedupe(detections), model=f"roboflow/{model}",
                               latency_s=round(latency, 3), raw=raw)
