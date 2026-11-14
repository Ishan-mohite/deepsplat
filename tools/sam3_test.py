# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "pillow"]
# ///
"""Test SAM 3 concept segmentation via the Roboflow serverless API.

Usage:
    export ROBOFLOW_API_KEY=...
    uv run sam3_test.py <image-path-or-url> "prompt 1" ["prompt 2" ...] [--thresh 0.5]

Outputs (next to this script, in ./out/):
    <name>.json          raw API response
    <name>_overlay.png   image with the returned polygons drawn on it
"""

import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw

ENDPOINT = "https://serverless.roboflow.com/sam3/concept_segment"
COLORS = [
    (230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48),
    (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60),
]


def load_image(src: str) -> tuple[Image.Image, dict]:
    """Return the PIL image (for drawing) and the API image payload."""
    if src.startswith(("http://", "https://")):
        r = requests.get(src, timeout=30)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        return img, {"type": "url", "value": src}
    data = Path(src).read_bytes()
    img = Image.open(io.BytesIO(data))
    return img, {"type": "base64", "value": base64.b64encode(data).decode()}


def find_polygons(obj) -> list[list[tuple[float, float]]]:
    """Find every list of [x, y] points in a prediction, whatever the nesting."""
    def is_point(p):
        return (isinstance(p, (list, tuple)) and len(p) == 2
                and all(isinstance(v, (int, float)) for v in p))

    def is_point_dict(p):
        return isinstance(p, dict) and {"x", "y"} <= p.keys() and len(p) <= 3

    found = []
    if isinstance(obj, list):
        if len(obj) >= 3 and all(is_point(p) for p in obj):
            return [[tuple(p) for p in obj]]
        if len(obj) >= 3 and all(is_point_dict(p) for p in obj):
            return [[(p["x"], p["y"]) for p in obj]]
        for item in obj:
            found += find_polygons(item)
    elif isinstance(obj, dict):
        for v in obj.values():
            found += find_polygons(v)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="local path or http(s) URL")
    ap.add_argument("prompts", nargs="+", help="text prompts, e.g. 'person' 'red car' (max 16)")
    ap.add_argument("--thresh", type=float, default=0.5, help="output_prob_thresh (default 0.5)")
    args = ap.parse_args()

    key = os.environ.get("ROBOFLOW_API_KEY")
    if not key:
        sys.exit("ROBOFLOW_API_KEY is not set (export ROBOFLOW_API_KEY=...)")
    if len(args.prompts) > 16:
        sys.exit("Roboflow accepts at most 16 prompts per request")

    img, image_payload = load_image(args.image)
    payload = {
        "image": image_payload,
        "prompts": [{"type": "text", "text": p} for p in args.prompts],
        "output_prob_thresh": args.thresh,
        "format": "polygon",
    }

    t0 = time.perf_counter()
    resp = requests.post(ENDPOINT, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=120)
    dt = time.perf_counter() - t0
    if not resp.ok:
        sys.exit(f"HTTP {resp.status_code}: {resp.text[:1000]}")
    result = resp.json()

    out_dir = Path(__file__).parent / "out"
    out_dir.mkdir(exist_ok=True)
    name = Path(args.image.split("?")[0]).stem or "image"
    (out_dir / f"{name}.json").write_text(json.dumps(result, indent=2))

    overlay = img.convert("RGBA")
    layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    print(f"Request took {dt:.2f}s")
    for i, pr in enumerate(result.get("prompt_results", [])):
        color = COLORS[i % len(COLORS)]
        preds = pr.get("predictions", [])
        n_polys = 0
        for pred in preds:
            for poly in find_polygons(pred):
                draw.polygon(poly, fill=color + (90,), outline=color + (255,), width=2)
                n_polys += 1
        label = args.prompts[i] if i < len(args.prompts) else pr.get("echo")
        print(f"  {label!r}: {len(preds)} instances, {n_polys} polygons drawn")

    out_png = out_dir / f"{name}_overlay.png"
    Image.alpha_composite(overlay, layer).convert("RGB").save(out_png)
    print(f"Saved {out_dir / f'{name}.json'}\nSaved {out_png}")


if __name__ == "__main__":
    main()
