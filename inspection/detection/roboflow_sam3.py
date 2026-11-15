import base64
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.parse import urlparse

import requests

from ..classes import enabled_classes
from .base import DetectedDamage, DetectionResult, DetectorUnavailable, dedupe, make_damage

ENDPOINT = "https://serverless.roboflow.com/sam3/concept_segment"
HOST = urlparse(ENDPOINT).hostname
CHECK_TIMEOUT_S = 3.0
CHECK_CACHE_S = 30.0
_check_lock = threading.Lock()
_last_check: tuple[float, bool] = (0.0, False)


def _connect() -> None:
    with socket.create_connection((HOST, 443), timeout=CHECK_TIMEOUT_S):
        pass


def reachable() -> bool:
    """Can the Roboflow endpoint be reached? DNS + TCP, at most ~3 s, cached for 30 s.
    Without internet, name resolution can hang for tens of seconds; the check runs in a thread
    with a hard timeout so a capture never waits for that."""
    global _last_check
    with _check_lock:
        t, ok = _last_check
        if time.monotonic() - t < CHECK_CACHE_S:
            return ok
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            pool.submit(_connect).result(timeout=CHECK_TIMEOUT_S + 0.5)
            ok = True
        except (OSError, FutureTimeout):
            ok = False
        finally:
            pool.shutdown(wait=False)
        _last_check = (time.monotonic(), ok)
        return ok


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

    def available(self) -> bool:
        return reachable()

    def _post(self, payload: dict) -> dict:
        if not reachable():
            raise DetectorUnavailable(f"SAM 3 runs at Roboflow ({HOST}), which is not reachable (no internet?)")
        last = None
        for attempt in range(3):
            try:
                r = requests.post(ENDPOINT, json=payload, timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self.api_key}"})
            except (requests.ConnectionError, requests.Timeout) as e:
                raise DetectorUnavailable(f"Roboflow not reachable: {e}") from e
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
