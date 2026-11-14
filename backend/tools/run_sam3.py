# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "pillow"]
# ///
"""Run SAM 3 (Roboflow serverless) on every image in dataset/<class>/ and render results.

Usage:
    export ROBOFLOW_API_KEY=...
    uv run run_sam3.py                 # query API (cached) + render
    uv run run_sam3.py --render-thresh 0.4 --no-api   # re-render only, no API credits

API responses are requested at a low threshold (--api-thresh, default 0.1) and cached in
out/<class>/<stem>.json, so rendering at another threshold is free.

Output per image: out/<class>/<stem>_seg.png  (original | one panel per prompt)
Summary:          out/summary.csv
"""

import argparse
import base64
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

ENDPOINT = "https://serverless.roboflow.com/sam3/concept_segment"
ROOT = Path(__file__).parent
DATASET, OUT = ROOT / "dataset", ROOT / "out"

PROMPTS = {
    "crack": ["crack", "wall crack"],
    "mold": ["mold", "dark stain"],
    "water_damage": ["water stain", "water damage"],
    "peeling_paint": ["peeling paint", "damaged plaster"],
    "efflorescence": ["efflorescence", "white salt deposits"],
    "broken_tile": ["broken tile", "crack"],
    "rust": ["rust", "rust stain"],
    "hole_in_wall": ["hole in wall", "damaged drywall"],
}
COLORS = [(230, 25, 75), (0, 130, 200), (60, 180, 75), (245, 130, 48)]
PANEL_W = 640


def query(img_path: Path, prompts: list[str], key: str, thresh: float) -> dict:
    payload = {
        "image": {"type": "base64", "value": base64.b64encode(img_path.read_bytes()).decode()},
        "prompts": [{"type": "text", "text": p} for p in prompts],
        "output_prob_thresh": thresh,
        "format": "polygon",
    }
    for attempt in range(3):
        r = requests.post(ENDPOINT, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
        return r.json()
    raise RuntimeError(f"HTTP {r.status_code} after retries: {r.text[:500]}")


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render(img_path: Path, result: dict, prompts: list[str], thresh: float) -> tuple[Image.Image, list[dict]]:
    base = Image.open(img_path).convert("RGB")
    scale = PANEL_W / base.width
    base = base.resize((PANEL_W, round(base.height * scale)))
    header = 34
    panels, stats = [], []

    def with_header(im: Image.Image, text: str) -> Image.Image:
        canvas = Image.new("RGB", (im.width, im.height + header), "white")
        canvas.paste(im, (0, header))
        ImageDraw.Draw(canvas).text((8, 7), text, fill="black", font=font(18))
        return canvas

    panels.append(with_header(base, "original"))
    for i, pr in enumerate(result.get("prompt_results", [])):
        color = COLORS[i % len(COLORS)]
        preds = [p for p in pr.get("predictions", []) if p.get("confidence", 0) >= thresh]
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for p in preds:
            for poly in p.get("masks", []):
                pts = [(x * scale, y * scale) for x, y in poly]
                if len(pts) >= 3:
                    draw.polygon(pts, fill=color + (100,), outline=color + (255,), width=2)
        panel = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
        all_conf = [p.get("confidence", 0) for p in pr.get("predictions", [])]
        max_c = max(all_conf, default=0)
        label = prompts[i] if i < len(prompts) else pr["echo"].get("text", "?")
        panels.append(with_header(panel, f"'{label}': {len(preds)} masks  (max conf {max_c:.2f})"))
        stats.append({"prompt": label, "n_masks": len(preds), "max_conf": round(max_c, 3)})

    sheet = Image.new("RGB", (sum(p.width for p in panels), max(p.height for p in panels)), "white")
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width
    return sheet, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api-thresh", type=float, default=0.1)
    ap.add_argument("--render-thresh", type=float, default=0.3)
    ap.add_argument("--no-api", action="store_true", help="only render cached JSON")
    ap.add_argument("--refresh", action="store_true", help="ignore cache and re-query")
    args = ap.parse_args()

    key = os.environ.get("ROBOFLOW_API_KEY")
    if not args.no_api and not key:
        sys.exit("ROBOFLOW_API_KEY is not set")

    rows, n_calls = [], 0
    for cls, prompts in PROMPTS.items():
        cls_dir = DATASET / cls
        if not cls_dir.is_dir():
            continue
        (OUT / cls).mkdir(parents=True, exist_ok=True)
        for img_path in sorted(cls_dir.glob("*.jpg")):
            cache = OUT / cls / f"{img_path.stem}.json"
            cached = cache.exists() and json.loads(cache.read_text()).get("_prompts") == prompts
            if cached and not args.refresh:
                result = json.loads(cache.read_text())
            elif args.no_api:
                print(f"  {img_path.name}: no cached result, skipped")
                continue
            else:
                t0 = time.perf_counter()
                try:
                    result = query(img_path, prompts, key, args.api_thresh)
                except RuntimeError as e:
                    print(f"  {img_path.name}: {e}")
                    continue
                result["_prompts"] = prompts
                result["_latency_s"] = round(time.perf_counter() - t0, 2)
                cache.write_text(json.dumps(result))
                n_calls += 1
                time.sleep(0.5)
            sheet, stats = render(img_path, result, prompts, args.render_thresh)
            sheet.save(OUT / cls / f"{img_path.stem}_seg.png")
            for s in stats:
                rows.append({"class": cls, "file": img_path.name, **s, "latency_s": result.get("_latency_s")})
            print(f"  {cls}/{img_path.name}: " + ", ".join(f"{s['prompt']}={s['n_masks']} ({s['max_conf']})" for s in stats))

    if rows:
        with open(OUT / "summary.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"\n{n_calls} API calls, render threshold {args.render_thresh}. Results in {OUT}")


if __name__ == "__main__":
    main()
