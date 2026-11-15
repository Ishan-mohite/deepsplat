"""Image quality gate (README step 2): flag blurry or too-dark photos."""

import io

import numpy as np
from PIL import Image

MIN_SHARPNESS = 30.0      # variance of the Laplacian on a 512 px grayscale copy
MIN_BRIGHTNESS = 35.0     # mean gray value 0..255


def assess(image_bytes: bytes) -> dict:
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    img.thumbnail((512, 512))
    g = np.asarray(img, dtype=np.float64)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    sharpness = float(lap.var())
    brightness = float(g.mean())
    flags = []
    if sharpness < MIN_SHARPNESS:
        flags.append("blurry")
    if brightness < MIN_BRIGHTNESS:
        flags.append("too_dark")
    return {"sharpness": round(sharpness, 1), "brightness": round(brightness, 1), "flags": flags,
            "ok": not flags}
