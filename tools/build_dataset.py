# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "pillow"]
# ///
"""Build a small, freely licensed test set of apartment defects from Wikimedia Commons.

Usage:
    uv run build_dataset.py [--per-class 15] [--out dataset]

Result:
    dataset/<class>/<class>_NN.jpg
    dataset/metadata.csv   (file, class, query, title, source page, author, license)
"""

import argparse
import csv
import html
import io
import re
import time
from pathlib import Path

import requests
from PIL import Image

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "defect-dataset-builder/0.1 (small research test script)"}

CLASSES = {
    "crack": ["crack in wall", "cracked plaster wall", "ceiling crack", "crack masonry wall"],
    "mold": ["mold on wall", "mould wall damp", "black mold bathroom", "mould ceiling"],
    "water_damage": ["water damage ceiling", "water stain ceiling", "water damaged wall", "damp wall interior"],
    "peeling_paint": ["peeling paint wall", "flaking paint", "peeling plaster"],
    "efflorescence": ["efflorescence wall", "efflorescence brick", "salt efflorescence"],
    "broken_tile": ["broken tile", "cracked tiles floor", "damaged tiles wall"],
    "rust": ["rusty radiator", "corroded pipe", "rust pipe"],
    "hole_in_wall": ["hole in drywall", "hole in wall plaster", "damaged drywall"],
}

ALLOWED_LICENSES = re.compile(r"^(CC0|CC BY|CC BY-SA|Public domain|PD)", re.I)


def strip_html(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def search(query: str, limit: int = 40) -> list[dict]:
    params = {
        "action": "query", "format": "json", "formatversion": 2,
        "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1024,
    }
    r = requests.get(API, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", [])
    return sorted(pages, key=lambda p: p.get("index", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=15)
    ap.add_argument("--out", default="dataset")
    args = ap.parse_args()

    out = Path(__file__).parent / args.out
    out.mkdir(exist_ok=True)
    rows, seen = [], set()

    for cls, queries in CLASSES.items():
        cls_dir = out / cls
        cls_dir.mkdir(exist_ok=True)
        n = 0
        for q in queries:
            if n >= args.per_class:
                break
            for page in search(q):
                if n >= args.per_class:
                    break
                title = page["title"]
                info = (page.get("imageinfo") or [{}])[0]
                meta = info.get("extmetadata", {})
                lic = meta.get("LicenseShortName", {}).get("value", "")
                if title in seen or info.get("mime") not in ("image/jpeg", "image/png"):
                    continue
                if info.get("width", 0) < 500 or not ALLOWED_LICENSES.match(lic):
                    continue
                try:
                    img_bytes = requests.get(info["thumburl"], headers=HEADERS, timeout=60).content
                    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                except Exception as e:
                    print(f"  skip {title}: {e}")
                    continue
                seen.add(title)
                n += 1
                fname = f"{cls}_{n:02d}.jpg"
                img.save(cls_dir / fname, quality=90)
                rows.append({
                    "file": f"{cls}/{fname}", "class": cls, "query": q, "title": title,
                    "source": info.get("descriptionurl", ""),
                    "author": strip_html(meta.get("Artist", {}).get("value", "")),
                    "license": lic,
                })
                time.sleep(0.2)
        print(f"{cls}: {n} images")

    with open(out / "metadata.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} images + metadata.csv to {out}")


if __name__ == "__main__":
    main()
