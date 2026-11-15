#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["fonttools", "uharfbuzz"]
# ///
"""Render the design system images shown in README.md and DESIGN_SYSTEM.md.

Colours come from design/system/tokens.json. All text is outlined (Space Grotesk, IBM Plex Sans,
IBM Plex Mono; SIL OFL) so the SVGs look the same on GitHub as anywhere else.

Usage:  uv run design/system/tools/build_docs_images.py
Fonts are fetched from the @fontsource npm packages on jsDelivr into tools/.fonts on first run.
"""
import io
import json
import math
import pathlib
import urllib.request

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = pathlib.Path(__file__).resolve().parent
SYSTEM = HERE.parent
OUT = SYSTEM / "img"
CDN = "https://cdn.jsdelivr.net/npm/@fontsource/"
FONT_FILES = {
    "display-600": "space-grotesk/files/space-grotesk-latin-600-normal.woff",
    "display-700": "space-grotesk/files/space-grotesk-latin-700-normal.woff",
    "body-400": "ibm-plex-sans/files/ibm-plex-sans-latin-400-normal.woff",
    "body-500": "ibm-plex-sans/files/ibm-plex-sans-latin-500-normal.woff",
    "body-600": "ibm-plex-sans/files/ibm-plex-sans-latin-600-normal.woff",
    "mono-400": "ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff",
    "mono-500": "ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff",
}


def num(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Font:
    def __init__(self, path):
        tt = TTFont(path)
        tt.flavor = None
        buf = io.BytesIO()
        tt.save(buf)
        self.hb = hb.Font(hb.Face(buf.getvalue()))
        self.gs = tt.getGlyphSet()
        self.order = tt.getGlyphOrder()

    def layout(self, text, size, tracking):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True})
        s, x, out = size / 1000, 0.0, []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            out.append((self.order[info.codepoint], x + pos.x_offset * s))
            x += pos.x_advance * s + tracking * size
        return out, x - (tracking * size if out else 0)

    def path(self, name, size, x, y):
        s = size / 1000
        pen = SVGPathPen(self.gs, ntos=num)
        self.gs[name].draw(TransformPen(pen, (s, 0, 0, -s, x, y)))
        return pen.getCommands()


FONTS = {}

# name: (font, size, tracking, uppercase)
STYLES = {
    "title": ("display-700", 24, -0.02, False),
    "eyebrow": ("display-600", 11, 0.14, True),
    "name": ("display-600", 14, -0.01, False),
    "hex": ("mono-500", 12, 0, False),
    "role": ("body-400", 12, 0, False),
    "label": ("body-500", 13, 0, False),
    "label-strong": ("body-600", 13, 0, False),
    "chip": ("body-500", 12, 0, False),
    "chip-strong": ("body-600", 12, 0, False),
    "button": ("body-600", 14, 0, False),
    "body-s": ("body-400", 13, 0, False),
    "mono-s": ("mono-500", 11, 0, False),
    "kpi": ("display-600", 40, -0.03, False),
    "donut": ("display-600", 30, -0.03, False),
    "card-title": ("display-600", 15, -0.01, False),
    "row-title": ("body-600", 14, 0, False),
    "row-meta": ("body-400", 12, 0, False),
}


def width(s, style):
    font, size, tr, up = STYLES[style]
    return FONTS[font].layout(s.upper() if up else s, size, tr)[1]


def T(s, x, y, style, fill, anchor="start"):
    font, size, tr, up = STYLES[style]
    f = FONTS[font]
    glyphs, w = f.layout(s.upper() if up else s, size, tr)
    x -= {"start": 0, "middle": w / 2, "end": w}[anchor]
    return f'<path fill="{fill}" d="{"".join(f.path(n, size, x + gx, y) for n, gx in glyphs)}"/>'


def Tspec(s, x, y, font, size, tracking, fill):
    f = FONTS[font]
    glyphs, _ = f.layout(s, size, tracking)
    return f'<path fill="{fill}" d="{"".join(f.path(n, size, x + gx, y) for n, gx in glyphs)}"/>'


def baseline(cy, style):
    return cy + STYLES[style][1] * 0.36


# ---------------------------------------------------------------- tokens
def load_tokens():
    data = json.loads((SYSTEM / "tokens.json").read_text())
    flat = {}

    def walk(node, prefix):
        for k, v in node.items():
            if k.startswith("$"):
                continue
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and "$value" in v:
                flat[key] = v["$value"]
            elif isinstance(v, dict):
                walk(v, key)
    walk(data["color"], "")

    def resolve(v):
        while isinstance(v, str) and v.startswith("{color."):
            v = flat[v[7:-1]]
        return v
    return {k: resolve(v) for k, v in flat.items()}


C = {}


def svg(w, h, bg, body, border=None):
    stroke = f' stroke="{border}"' if border else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="22" fill="{bg}"{stroke}/>{body}</svg>\n')


def rect(x, y, w, h, r, fill, stroke=None, sw=1):
    s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" rx="{num(r)}" fill="{fill}"{s}/>'


def circle(cx, cy, r, fill, extra=""):
    return f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(r)}" fill="{fill}"{extra}/>'


def header(x, y, eyebrow, title, eb_color, title_color):
    return T(eyebrow, x, y, "eyebrow", eb_color) + T(title, x, y + 32, "title", title_color)


# ---------------------------------------------------------------- images
def palette():
    W, pad, gap, cols = 960, 32, 16, 4
    cw = (W - 2 * pad - (cols - 1) * gap) / cols
    rows = [
        [("paper", "Page background"), ("paper-2", "Sunken panels, tracks"), ("card", "Cards, sheets, inputs"), ("line", "Card borders, dividers")],
        [("ink", "Text, dark buttons"), ("ink-2", "Secondary text"), ("muted", "Hints, axis labels"), ("line-2", "Control borders")],
        [("blue", "Action, located, closed"), ("blue-soft", "Selected, info"), ("rust", "Open findings, newest"), ("rust-soft", "Open finding fill")],
        [("green", "Live, saved, repaired"), ("green-soft", "Live chip fill"), ("amber", "Warnings"), ("amber-soft", "Warning fill")],
    ]
    body = header(pad, 44, "Colour · product UI", "Warm paper, navy ink, one blue, rust for open findings", C["blue"], C["ink"])
    y = 104
    for row in rows:
        for i, (name, role) in enumerate(row):
            x = pad + i * (cw + gap)
            light = name in ("paper", "paper-2", "card", "line", "line-2", "blue-soft", "rust-soft", "green-soft", "amber-soft")
            body += rect(x, y, cw, 80, 14, C[name], C["line-2"] if light else None)
            body += T(f"--{name}", x, y + 104, "name", C["ink"])
            body += T(C[name].upper(), x, y + 124, "hex", C["ink-2"])
            body += T(role, x, y + 143, "role", C["ink-2"])
        y += 172
    return svg(W, y + 8, C["card"], body, C["line"])


def viewport():
    W, pad = 960, 32
    body = header(pad, 44, "Colour · 3D viewport", "The viewport is always dark; data sits on top of it", C["viewport.pick"], C["viewport.ink"])
    items = [("vp", "viewport.bg"), ("vp-2", "viewport.bg-2"), ("vp-line", "viewport.line"), ("vp-ink", "viewport.ink"),
             ("vp-muted", "viewport.muted"), ("pick", "viewport.pick"), ("vp-rust", "viewport.rust")]
    gap = 12
    cw = (W - 2 * pad - (len(items) - 1) * gap) / len(items)
    y = 100
    for i, (name, key) in enumerate(items):
        x = pad + i * (cw + gap)
        body += rect(x, y, cw, 64, 12, C[key], C["viewport.line"])
        body += T(f"--{name}", x, y + 88, "name", C["viewport.ink"])
        body += T(C[key].upper(), x, y + 107, "hex", C["viewport.muted"])
    y += 150
    body += T("Severity · always with a label", pad, y, "eyebrow", C["viewport.muted"])
    x = pad
    for name in ("low", "medium", "high", "critical"):
        col = C[f"severity.{name}"]
        cx, cy = x + 6, y + 30
        body += f'<rect x="{num(cx - 6)}" y="{num(cy - 6)}" width="12" height="12" rx="2" fill="{col}" transform="rotate(45 {num(cx)} {num(cy)})"/>'
        label = name.capitalize()
        body += T(label, x + 22, baseline(cy, "label-strong"), "label-strong", C["viewport.ink"])
        body += T(col.upper(), x + 22 + width(label, "label-strong") + 10, baseline(cy, "hex"), "hex", C["viewport.muted"])
        x += 210
    y += 76
    body += T("Segmentation classes · fixed order", pad, y, "eyebrow", C["viewport.muted"])
    keys = [str(i) for i in range(1, 8)] + ["neutral"]
    cw = (W - 2 * pad - 7 * gap) / 8
    for i, k in enumerate(keys):
        x = pad + i * (cw + gap)
        body += rect(x, y + 16, cw, 44, 9, C[f"segmentation.{k}"])
        body += T(k if k != "neutral" else "bg", x + 12, baseline(y + 38, "label-strong"), "label-strong", "#FFFFFF")
        body += T(C[f"segmentation.{k}"].upper(), x, y + 82, "hex", C["viewport.muted"])
    return svg(W, y + 110, C["viewport.bg"], body)


def typography():
    W, pad = 960, 32
    rows = [
        ("display-xl", "display-700", 44, -0.025, "44 / 1.08 · 700 · -0.025em", "Every record in its place"),
        ("display-l", "display-700", 32, -0.02, "32 / 1.1 · 700 · -0.02em", "Bath, north wall"),
        ("display-m", "display-600", 24, -0.015, "24 / 1.15 · 600 · -0.015em", "Crack · north wall"),
        ("display-s", "display-600", 18, -0.01, "18 / 1.25 · 600 · -0.01em", "Findings by status"),
        ("kpi", "display-600", 40, -0.03, "40 / 1 · 600 · -0.03em", "148"),
        ("body", "body-400", 15, 0, "15 / 1.5 · 400", "A new photo is matched against the map to find its exact spot."),
        ("body-s", "body-400", 13, 0, "13 / 1.45 · 400", "Property A · 3rd floor · scanned 14 Mar 2026"),
        ("label / button", "body-600", 14, 0, "13–14 / 1.2 · 500–600", "Record defect"),
        ("eyebrow", "display-600", 11, 0.14, "11 / 1 · 600 · 0.14em · caps", "CAPTURE IN MOTION"),
        ("mono", "mono-400", 13, 0, "13 / 1.4 · 400", "14 Mar 2026, 10:42 · REC-0148"),
    ]
    body = header(pad, 44, "Typography", "Space Grotesk for display, IBM Plex Sans for UI, Plex Mono for data", C["blue"], C["ink"])
    y = 96
    for name, font, size, tr, spec, sample in rows:
        h = max(52, size * 1.5 + 18)
        body += f'<line x1="{pad}" y1="{num(y)}" x2="{W - pad}" y2="{num(y)}" stroke="{C["line"]}"/>'
        mid = y + h / 2
        body += T(name, pad, mid - 3, "name", C["ink"]) + T(spec, pad, mid + 15, "mono-s", C["ink-2"])
        color = C["blue"] if name == "eyebrow" else C["ink"]
        body += Tspec(sample, 300, mid + size * 0.36, font, size, tr, color)
        y += h
    return svg(W, y + 28, C["card"], body, C["line"])


def pill(x, cy, text, style, fill, color, stroke=None, h=44, padx=20, sw=1):
    w = width(text, style) + 2 * padx
    return rect(x, cy - h / 2, w, h, h / 2, fill, stroke, sw) + T(text, x + padx, baseline(cy, style), style, color), w


def glyph(kind, cx, cy, color):
    st = f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    return {
        "new": f'<path d="M{num(cx - 4)} {num(cy)}H{num(cx + 4)}M{num(cx)} {num(cy - 4)}V{num(cy + 4)}" {st}/>',
        "worsening": f'<path d="M{num(cx - 4)} {num(cy + 4)}L{num(cx + 4)} {num(cy - 4)}M{num(cx - 1)} {num(cy - 4)}H{num(cx + 4)}V{num(cy + 1)}" {st}/>',
        "stable": f'<path d="M{num(cx - 4)} {num(cy - 2)}H{num(cx + 4)}M{num(cx - 4)} {num(cy + 2)}H{num(cx + 4)}" {st}/>',
        "repaired": f'<path d="M{num(cx - 4)} {num(cy)}L{num(cx - 1)} {num(cy + 3)}L{num(cx + 4.5)} {num(cy - 3.5)}" {st}/>',
    }[kind]


def components():
    W, pad = 960, 32
    body = header(pad, 44, "Components", "Rounded cards, pill buttons, chips that never rely on colour alone", C["blue"], C["ink"])
    y = 116

    def section(label, yy):
        return T(label, pad, yy, "eyebrow", C["muted"])

    # buttons
    body += section("Buttons", y)
    x, cy = pad, y + 40
    for text, fill, color, stroke in [("Record defect", C["blue"], "#FFFFFF", None), ("Open 3D scan", C["ink"], "#FFFFFF", None),
                                      ("Export PDF", C["card"], C["ink"], C["line-2"]), ("Start handover", "none", C["ink-2"], None)]:
        s, w = pill(x, cy, text, "button", fill, color, stroke)
        body += s
        x += w + 12
    # nav + input on the same row
    x0 = 620
    body += T("Navigation", x0, y, "eyebrow", C["muted"])
    names = ["Sites", "Issues", "Reports"]
    widths = [width(n, "label") + 36 for n in names]
    body += rect(x0, cy - 22, sum(widths) + 8 + 4 * (len(names) - 1), 44, 22, C["paper-2"])
    nx = x0 + 4
    for i, (n, w) in enumerate(zip(names, widths)):
        if i == 0:
            body += rect(nx, cy - 18, w, 36, 18, C["ink"]) + T(n, nx + 18, baseline(cy, "label-strong"), "label-strong", "#FFFFFF")
        else:
            body += T(n, nx + 18, baseline(cy, "label"), "label", C["ink-2"])
        nx += w + 4
    y += 92

    # chips
    body += section("Chips", y)
    x, cy = pad, y + 32
    chip_specs = [("Scan active", C["green-soft"], C["green"], C["green"], True), ("Crack", C["blue"], "#FFFFFF", C["blue"], False),
                  ("Mould", C["card"], C["ink"], C["line-2"], False), ("Stain", C["card"], C["ink"], C["line-2"], False),
                  ("Localized · Bathroom", C["blue-soft"], C["blue"], C["blue-soft"], False)]
    for text, fill, color, stroke, dot in chip_specs:
        style = "chip-strong" if color != C["ink"] else "chip"
        w = width(text, style) + 24 + (12 if dot else 0)
        body += rect(x, cy - 13, w, 26, 13, fill, stroke)
        tx = x + 12
        if dot:
            body += circle(x + 15, cy, 3, color)
            tx += 12
        body += T(text, tx, baseline(cy, style), style, color)
        x += w + 10
    # severity
    x0 = 620
    body += T("Severity", x0, y, "eyebrow", C["muted"])
    x = x0
    for name in ("low", "medium", "high", "critical"):
        col = C[f"severity.{name}"]
        body += f'<rect x="{num(x)}" y="{num(cy - 4)}" width="8" height="8" rx="1.5" fill="{col}" transform="rotate(45 {num(x + 4)} {num(cy)})"/>'
        style = "chip-strong" if name == "critical" else "chip"
        body += T(name.capitalize(), x + 14, baseline(cy, style), style, C["ink"])
        x += width(name.capitalize(), style) + 32
    y += 80

    # status
    body += section("Issue status", y)
    x, cy = pad, y + 32
    for kind, fill, color in [("new", C["blue-soft"], C["blue"]), ("worsening", C["rust-soft"], C["rust"]),
                              ("stable", C["paper-2"], C["ink-2"]), ("repaired", C["green-soft"], C["green"])]:
        label = kind.capitalize()
        w = width(label, "chip-strong") + 34
        body += rect(x, cy - 13, w, 26, 13, fill) + glyph(kind, x + 14, cy, color)
        body += T(label, x + 24, baseline(cy, "chip-strong"), "chip-strong", color)
        x += w + 10
    y += 80

    # callouts, pins and time bar
    body += section("Callout pills, leader lines and pins", y)
    area_y = y + 16
    body += rect(pad, area_y, 556, 150, 14, C["paper-2"])
    for i, (text, col, px, py, sel) in enumerate([("Crack · north wall · open", C["rust"], 470, area_y + 42, True),
                                                  ("Mould · repaired in 6 days", C["blue"], 520, area_y + 108, False)]):
        cy = area_y + 42 + i * 66
        s, w = pill(pad + 20, cy, text, "label-strong", C["card"], C["ink"], col, h=34, padx=16, sw=1.5)
        body += s
        body += f'<line x1="{num(pad + 20 + w)}" y1="{num(cy)}" x2="{num(px)}" y2="{num(py)}" stroke="{col}" stroke-width="1.5"/>'
        if sel:
            body += circle(px, py, 13, col, ' fill-opacity="0.25"')
        body += circle(px, py, 7, col, f' stroke="{C["card"]}" stroke-width="2"')
    # time bar card
    x0 = 620
    body += T("Time bar", x0, y, "eyebrow", C["muted"])
    body += rect(x0, area_y, W - pad - x0, 150, 14, C["card"], C["line"])
    body += T("Timeline at this spot", x0 + 20, area_y + 34, "card-title", C["ink"])
    lx0, lx1, ly = x0 + 34, W - pad - 34, area_y + 84
    body += f'<line x1="{lx0}" y1="{ly}" x2="{lx1}" y2="{ly}" stroke="{C["blue"]}" stroke-width="2"/>'
    for frac, year, col in [(0, "2023", C["blue"]), (0.42, "2024", C["blue"]), (1, "2026", C["rust"])]:
        nx = lx0 + frac * (lx1 - lx0)
        body += circle(nx, ly, 6, col, f' stroke="{C["card"]}" stroke-width="2"')
        body += T(year, nx, ly + 30, "mono-s", C["ink-2"], "middle")
    y = area_y + 150 + 40

    # KPI tiles and a record row
    body += section("KPI tiles and records", y)
    tiles = [("Open findings", "3", C["rust"]), ("Days to repair, avg.", "6", C["blue"]), ("Records at this site", "148", C["ink"])]
    tw, ty = 172, y + 16
    for i, (label, value, col) in enumerate(tiles):
        tx = pad + i * (tw + 14)
        body += rect(tx, ty, tw, 132, 22, C["card"], C["line"])
        body += T(label, tx + 18, ty + 30, "body-s", C["ink-2"]) + T(value, tx + 18, ty + 104, "kpi", col)
    x0 = 620
    body += rect(x0, ty, W - pad - x0, 132, 22, C["card"], C["line"])
    body += T("Latest records", x0 + 20, ty + 30, "card-title", C["ink"])
    for i, (title, meta) in enumerate([("Crack · Bath, north wall", "Move-out · today 10:42 · M. Keller"),
                                       ("Mould · Kitchen", "Repair recorded · closed")]):
        ry = ty + 50 + i * 38
        body += rect(x0 + 20, ry, 40, 30, 7, C["paper-2"])
        body += T(title, x0 + 72, ry + 13, "row-title", C["ink"]) + T(meta, x0 + 72, ry + 29, "row-meta", C["ink-2"])
    return svg(W, ty + 164, C["paper"], body)


def dataviz():
    W, pad = 960, 32
    body = header(pad, 44, "Data visualisation", "One series, colour by role: blue for the normal case, rust for what needs action", C["blue"], C["ink"])
    top = 104
    cw1 = 540
    body += rect(pad, top, cw1, 250, 22, C["card"], C["line"])
    body += T("Records per month", pad + 22, top + 34, "card-title", C["ink"])
    body += T("Sample data", pad + cw1 - 22, top + 34, "mono-s", C["muted"], "end")
    vals = [6, 9, 7, 12, 10, 15, 12, 17, 14, 19, 17, 23]
    bx0, by = pad + 30, top + 220
    bw, bg = 26, 15
    for i, v in enumerate(vals):
        h = v * 6.6
        col = C["rust"] if i == len(vals) - 1 else C["blue"]
        body += f'<path d="M{num(bx0 + i * (bw + bg))} {by}V{num(by - h + 5)}q0 -5 5 -5h{bw - 10}q5 0 5 5V{by}Z" fill="{col}"/>'
    body += f'<line x1="{pad + 22}" y1="{by}" x2="{pad + cw1 - 22}" y2="{by}" stroke="{C["line-2"]}"/>'
    x0 = pad + cw1 + 16
    cw2 = W - pad - x0
    body += rect(x0, top, cw2, 250, 22, C["card"], C["line"])
    body += T("Findings by status", x0 + 22, top + 34, "card-title", C["ink"])
    cx, cy, r, sw = x0 + 100, top + 145, 58, 20
    circ = 2 * math.pi * r
    closed, total = 12, 15
    body += (f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{r}" fill="none" stroke="{C["blue"]}" stroke-width="{sw}"/>'
             f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{r}" fill="none" stroke="{C["rust"]}" stroke-width="{sw}" '
             f'stroke-dasharray="{num(circ * (total - closed) / total)} {num(circ)}" transform="rotate(-90 {num(cx)} {num(cy)})"/>')
    body += T("15", cx, cy + 10, "donut", C["ink"], "middle")
    body += T("total", cx, cy + 28, "row-meta", C["ink-2"], "middle")
    lx = cx + 100
    for i, (label, col) in enumerate([("Closed · 12", C["blue"]), ("Open · 3", C["rust"])]):
        ly = cy - 22 + i * 28
        body += circle(lx, ly, 6, col) + T(label, lx + 16, baseline(ly, "label"), "label", C["ink"])
    body += T("Old defects leave the", lx, cy + 46, "row-meta", C["ink-2"]) + T("view once repaired", lx, cy + 62, "row-meta", C["ink-2"])
    return svg(W, top + 282, C["paper"], body)


LOGOS = SYSTEM.parent / "brand" / "logos"


def nest(file, x, y, w, prefix):
    """Embed a logo SVG file at (x, y) with width w; ids are prefixed so several copies can coexist."""
    src = (LOGOS / file).read_text()
    src = src[src.index("<svg"):].strip()
    src = src.replace('id="ds-', f'id="{prefix}-').replace("url(#ds-", f"url(#{prefix}-")
    vb = [float(v) for v in src.split('viewBox="', 1)[1].split('"', 1)[0].split()]
    h = w * vb[3] / vb[2]
    return src.replace("<svg ", f'<svg x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" ', 1)


def logos():
    W, pad, gap = 960, 32, 16
    pw = (W - 2 * pad - gap) / 2
    body = header(pad, 44, "Logo and app icon", "Lowercase wordmark: deep is a point cloud that becomes the solid splat", C["blue"], C["ink"])
    top = 100
    for i, (theme, bg, ink, muted, line) in enumerate([("light", C["paper"], C["ink"], C["ink-2"], None),
                                                       ("dark", C["viewport.bg"], C["viewport.ink"], C["viewport.muted"], None)]):
        x = pad + i * (pw + gap)
        body += rect(x, top, pw, 330, 14, bg)
        body += T("32 px and up", x + 24, top + 32, "mono-s", muted)
        body += nest(f"deepsplat-wordmark-{theme}.svg", x + 24, top + 48, 340, f"w{i}")
        body += T("Below 32 px", x + 24, top + 160, "mono-s", muted)
        body += nest(f"deepsplat-wordmark-small-{theme}.svg", x + 24, top + 174, 150, f"s{i}")
        body += T("App icon 64 · 32 · favicon 16", x + 24, top + 226, "mono-s", muted)
        ix = x + 24
        for j, (f, sz) in enumerate([("deepsplat-icon.svg", 64), ("deepsplat-icon.svg", 32), ("deepsplat-icon-16.svg", 16)]):
            body += nest(f, ix, top + 306 - sz, sz, f"i{i}{j}")
            if theme == "dark":  # documented rule: outline the navy tile on dark surfaces
                body += rect(ix + .5, top + 306 - sz + .5, sz - 1, sz - 1, sz * 14.5 / 64, "none", C["viewport.line"])
            ix += sz + 16
    return svg(W, top + 362, C["card"], body, C["line"])


def ensure_fonts(d):
    d.mkdir(parents=True, exist_ok=True)
    for key, rel in FONT_FILES.items():
        p = d / pathlib.Path(rel).name
        if not p.exists():
            urllib.request.urlretrieve(CDN + rel, p)
        FONTS[key] = Font(p)


def main():
    ensure_fonts(HERE / ".fonts")
    C.update(load_tokens())
    OUT.mkdir(exist_ok=True)
    for name, fn in [("logos", logos), ("palette", palette), ("viewport", viewport), ("typography", typography),
                     ("components", components), ("dataviz", dataviz)]:
        (OUT / f"{name}.svg").write_text(fn(), encoding="utf-8")
        print(f"wrote {OUT / name}.svg")


if __name__ == "__main__":
    main()
