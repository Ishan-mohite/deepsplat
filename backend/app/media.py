"""Image/video helpers: metadata extraction and detection overlays."""

import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

COLORS = {
    "crack": (230, 25, 75), "mold": (60, 180, 75), "water_damage": (0, 130, 200),
    "rust": (245, 130, 48), "peeling_paint": (145, 30, 180), "hole": (240, 50, 230),
    "broken_tile": (210, 245, 60),
}


def normalize_image(data: bytes) -> tuple[bytes, int, int, str]:
    """Apply EXIF rotation (iPhone photos) and return (jpeg bytes, width, height, sha256 of original)."""
    sha = hashlib.sha256(data).hexdigest()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue(), img.width, img.height, sha


def probe_video(path: Path) -> dict:
    """Duration / resolution / fps via ffprobe, if installed. Never raises."""
    if not shutil.which("ffprobe"):
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        info = json.loads(out)
        s = (info.get("streams") or [{}])[0]
        num, _, den = s.get("avg_frame_rate", "0/1").partition("/")
        fps = float(num) / float(den) if den and float(den) else None
        dur = info.get("format", {}).get("duration")
        return {"video_width": s.get("width"), "video_height": s.get("height"),
                "video_fps": round(fps, 3) if fps else None,
                "video_duration_s": round(float(dur), 3) if dur else None}
    except Exception:
        return {}


def render_overlay(image_bytes: bytes, detections: list, labels: dict[str, str]) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width = max(2, img.width // 400)
    try:
        font = ImageFont.load_default(size=max(14, img.width // 50))
    except TypeError:
        font = ImageFont.load_default()
    for d in detections:
        color = COLORS.get(d.damage_class, (255, 255, 0))
        for poly in d.polygons:
            draw.polygon([tuple(p) for p in poly], fill=color + (90,), outline=color + (255,), width=width)
        x0, y0 = d.bbox[0], d.bbox[1]
        text = f"{labels.get(d.damage_class, d.damage_class)} {d.confidence:.2f}"
        tb = draw.textbbox((x0, y0), text, font=font)
        draw.rectangle(tb, fill=color + (220,))
        draw.text((x0, y0), text, fill="white", font=font)
    out = Image.alpha_composite(img, layer).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
