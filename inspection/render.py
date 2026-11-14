"""Overlay (photo with masks) and binary mask PNGs."""

import io

from PIL import Image, ImageDraw, ImageFont

from .classes import label

COLORS = {
    "crack": (230, 25, 75), "corrosion": (245, 130, 48), "leak_stain": (0, 130, 200),
    "coating_damage": (145, 30, 180), "dent": (60, 180, 75), "exposed_wiring": (240, 50, 230),
    "obstruction_spill": (210, 245, 60), "missing_part": (128, 128, 128),
}


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def overlay(image_bytes: bytes, detections: list) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    width = max(2, img.width // 400)
    font = _font(max(14, img.width // 50))
    for d in detections:
        color = COLORS.get(d.damage_class, (255, 255, 0))
        for poly in d.polygons:
            draw.polygon([tuple(p) for p in poly], fill=color + (90,), outline=color + (255,), width=width)
        text = f"{label(d.damage_class)} {d.confidence:.2f}"
        tb = draw.textbbox((d.bbox[0], d.bbox[1]), text, font=font)
        draw.rectangle(tb, fill=color + (220,))
        draw.text((d.bbox[0], d.bbox[1]), text, fill="white", font=font)
    buf = io.BytesIO()
    Image.alpha_composite(img, layer).convert("RGB").save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def mask_png(polygons: list, width: int, height: int) -> bytes:
    m = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(m)
    for poly in polygons:
        if len(poly) >= 3:
            draw.polygon([tuple(p) for p in poly], fill=255)
    buf = io.BytesIO()
    m.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
