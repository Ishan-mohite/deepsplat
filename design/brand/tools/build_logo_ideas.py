#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["fonttools", "uharfbuzz"]
# ///
"""Build the deepsplat logo concept sheet (design/brand/logo-ideas.html).

Every wordmark is outlined from Space Grotesk (SIL OFL) with HarfBuzz shaping, so
each inline SVG is self-contained and can be exported as-is.

Usage:  uv run design/brand/tools/build_logo_ideas.py [--fonts DIR] [--out PATH]
        (uv installs fonttools and uharfbuzz from the inline metadata above)
Fonts are fetched from the @fontsource npm packages on jsDelivr if missing.
"""
import argparse
import html
import io
import itertools
import math
import pathlib
import random
import urllib.request

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.pointInsidePen import PointInsidePen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = pathlib.Path(__file__).resolve().parent
FONT_FILES = {
    "sg600": "space-grotesk/files/space-grotesk-latin-600-normal.woff",
    "sg700": "space-grotesk/files/space-grotesk-latin-700-normal.woff",
}
CDN = "https://cdn.jsdelivr.net/npm/@fontsource/"


def num(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Font:
    def __init__(self, path):
        self.tt = TTFont(path)
        self.tt.flavor = None
        buf = io.BytesIO()
        self.tt.save(buf)
        self.hb = hb.Font(hb.Face(buf.getvalue()))
        self.gs = self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()

    def shape(self, text, size, tracking):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True, "liga": False})
        s, x, out = size / 1000, 0.0, []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            out.append(dict(name=self.order[info.codepoint], ch=text[info.cluster], x=x + pos.x_offset * s))
            x += pos.x_advance * s + tracking * size
        return out

    def path(self, name, size, x, y):
        s = size / 1000
        pen = SVGPathPen(self.gs, ntos=num)
        self.gs[name].draw(TransformPen(pen, (s, 0, 0, -s, x, y)))
        return pen.getCommands()

    def bounds(self, name, size, x, y):
        s = size / 1000
        bp = BoundsPen(self.gs)
        self.gs[name].draw(TransformPen(bp, (s, 0, 0, -s, x, y)))
        return bp.bounds

    def inside(self, name, fx, fy):
        pen = PointInsidePen(self.gs, (fx, fy))
        self.gs[name].draw(pen)
        return pen.getResult()


FONTS = {}


def word(font="sg700", x=0.0, y=0.0, size=100, tracking=-0.03, text="deepsplat"):
    f = FONTS[font]
    glyphs = f.shape(text, size, tracking)
    for g in glyphs:
        g["x"] += x
        g["d"] = f.path(g["name"], size, g["x"], y)
        g["b"] = f.bounds(g["name"], size, g["x"], y)
        g["size"], g["y"], g["font"] = size, y, font
    return glyphs


def ink(glyphs):
    bs = [g["b"] for g in glyphs if g["b"]]
    return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)


def paths(glyphs, fill):
    return f'<path fill="{fill}" d="{"".join(g["d"] for g in glyphs)}"/>'


def rot(px, py, cx, cy, deg):
    a = math.radians(deg)
    dx, dy = px - cx, py - cy
    return cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)


def ellipse_box(cx, cy, rx, ry, deg):
    a = math.radians(deg)
    hw = math.hypot(rx * math.cos(a), ry * math.sin(a))
    hh = math.hypot(rx * math.sin(a), ry * math.cos(a))
    return cx - hw, cy - hh, cx + hw, cy + hh


def union(*boxes):
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


# ---------------------------------------------------------------- themes
LIGHT = dict(name="light", bg="#F1EEE6", ink="#172033", ink2="#4A5163", blue="#2453D4", blue2="#7EA2FF",
             rust="#C24A1C", line="#D3CBBB", card="#FBFAF7")
DARK = dict(name="dark", bg="#1A2130", ink="#E9ECF3", ink2="#9AA4B8", blue="#7EA2FF", blue2="#4F7BF0",
            rust="#E8784A", line="#36435C", card="#232C3E")
# The app icon is one fixed design shown on both backgrounds.
ICON = dict(blue="#2453D4", navy="#172033", paper="#F1EEE6", card="#FBFAF7", line="#D3CBBB", ink="#172033",
            sky="#7EA2FF", rust="#C24A1C", peach="#F2946A")

_uid = itertools.count(1)


class Ctx:
    def __init__(self, theme, small=False):
        self.t, self.small = theme, small
        self.prefix = f"k{next(_uid)}"
        self.defs = []

    def gid(self):
        return f"{self.prefix}-{len(self.defs)}"

    def gauss(self, color, peak=1.0):
        """Radial gradient approximating a 2D Gaussian falloff (alpha = exp(-3.2 r^2))."""
        i = self.gid()
        stops = "".join(
            f'<stop offset="{r}" stop-color="{color}" stop-opacity="{num(peak * math.exp(-3.2 * r * r) if r < 1 else 0)}"/>'
            for r in (0, 0.2, 0.4, 0.6, 0.8, 1))
        self.defs.append(f'<radialGradient id="{i}">{stops}</radialGradient>')
        return f"url(#{i})"

    def add_def(self, body_with_id):
        i = self.gid()
        self.defs.append(body_with_id.replace("{id}", i))
        return i


def svg(ctx, body, box, pad=2, height=None, label="deepsplat"):
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w, h = x1 - x0, y1 - y0
    defs = f"<defs>{''.join(ctx.defs)}</defs>" if ctx.defs else ""
    size = f' height="{height}" width="{num(height * w / h)}"' if height else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{num(x0)} {num(y0)} {num(w)} {num(h)}"{size} '
            f'role="img" aria-label="{label}">{defs}{body}</svg>')


def gauss_ellipse(ctx, cx, cy, rx, ry, deg, color, peak=1.0):
    return (f'<ellipse cx="{num(cx)}" cy="{num(cy)}" rx="{num(rx)}" ry="{num(ry)}" '
            f'transform="rotate({num(deg)} {num(cx)} {num(cy)})" fill="{ctx.gauss(color, peak)}"/>')


def tile(kind):
    fill = dict(blue=ICON["blue"], navy=ICON["navy"], card=ICON["card"])[kind]
    stroke = f' stroke="{ICON["line"]}" stroke-width="1.5"' if kind == "card" else ""
    return f'<rect x="0.75" y="0.75" width="62.5" height="62.5" rx="14" fill="{fill}"{stroke}/>'


def icon_svg(ctx, body):
    defs = f"<defs>{''.join(ctx.defs)}</defs>" if ctx.defs else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="deepsplat icon">{defs}{body}</svg>'


def glyph_in(font, ch, size, cx, cy):
    """Outline a single glyph centred (by ink box) on cx, cy."""
    f = FONTS[font]
    name = f.tt.getBestCmap()[ord(ch)]
    b = f.bounds(name, size, 0, 0)
    x = cx - (b[0] + b[2]) / 2
    y = cy - (b[1] + b[3]) / 2
    return f.path(name, size, x, y), f.bounds(name, size, x, y)


# ---------------------------------------------------------------- concepts
def c0(ctx):
    t = ctx.t
    G = word()
    bx = ink(G)
    cx, cy = -44, -30
    body = gauss_ellipse(ctx, cx, cy, 59, 34, -28, t["blue"]) + paths(G, t["ink"])
    return body, union(bx, (cx - 34, cy - 26, cx + 34, cy + 26))


def i0(ctx):
    return tile("navy") + gauss_ellipse(ctx, 32, 32, 30, 17, -28, ICON["sky"])


def c1(ctx):
    t = ctx.t
    G = word()
    bx = ink(G)
    cx, cy = -50, -28
    blobs = [
        (cx - 2, cy + 6, 36, 20, -28, t["blue"], 1.0),
        (cx + 22, cy - 20, 19, 11, -24, t["blue2"], 1.0),
        (cx - 27, cy + 25, 12, 7, -36, t["blue"], 0.8),
    ]
    body = "".join(gauss_ellipse(ctx, *b) for b in blobs) + paths(G, t["ink"])
    # Gaussians fade out, so count ~70% of the radius as visible extent.
    vis = [ellipse_box(b[0], b[1], b[2] * .7, b[3] * .7, b[4]) for b in blobs]
    return body, union(bx, *vis)


def i1(ctx):
    b = [(30, 35, 26, 15, -28, ICON["card"], 1.0), (45, 19, 13, 7.5, -24, ICON["sky"], 1.0),
         (13, 50, 8.5, 5, -36, ICON["card"], 0.85)]
    return tile("blue") + "".join(gauss_ellipse(ctx, *x) for x in b)


def c2(ctx):
    t = ctx.t
    G = word()
    bx = ink(G)
    cx, cy, rx, ry, deg = -52, -26, 34, 19, -28
    sw, aw, dot = (9, 5.5, 8) if ctx.small else (6.5, 3.5, 6.5)
    ext = 11
    body = (f'<g transform="rotate({deg} {cx} {cy})" fill="none" stroke-linecap="round">'
            f'<line x1="{cx - rx - ext}" y1="{cy}" x2="{cx + rx + ext}" y2="{cy}" stroke="{t["ink"]}" stroke-width="{aw}"/>'
            f'<line x1="{cx}" y1="{cy - ry - ext}" x2="{cx}" y2="{cy + ry + ext}" stroke="{t["ink"]}" stroke-width="{aw}"/>'
            f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" stroke="{t["blue"]}" stroke-width="{sw}"/></g>'
            f'<circle cx="{cx}" cy="{cy}" r="{dot}" fill="{t["rust"]}"/>' + paths(G, t["ink"]))
    pts = [rot(px, py, cx, cy, deg) for px, py in
           ((cx - rx - ext, cy), (cx + rx + ext, cy), (cx, cy - ry - ext), (cx, cy + ry + ext))]
    e = ellipse_box(cx, cy, rx + sw / 2, ry + sw / 2, deg)
    return body, union(bx, e, *[(p[0] - aw, p[1] - aw, p[0] + aw, p[1] + aw) for p in pts])


def i2(ctx):
    cx = cy = 32
    rx, ry, deg = 19, 11, -28
    return (tile("navy") + f'<g transform="rotate({deg} {cx} {cy})" fill="none" stroke-linecap="round">'
            f'<line x1="{cx - 27}" y1="{cy}" x2="{cx + 27}" y2="{cy}" stroke="{ICON["paper"]}" stroke-width="3"/>'
            f'<line x1="{cx}" y1="{cy - 18}" x2="{cx}" y2="{cy + 18}" stroke="{ICON["paper"]}" stroke-width="3"/>'
            f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" stroke="{ICON["sky"]}" stroke-width="5"/></g>'
            f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="{ICON["peach"]}"/>')


def crack(cx, top, bottom, w, amp=1.0):
    """Zig-zag stem: constant horizontal thickness w, flat top and bottom."""
    fr = (0, .14, .3, .47, .63, .8, 1)
    off = (0.8, -2.6, 2.3, -2.4, 2.7, -1.7, 0.4)
    pts = [(cx + o * amp, top + f * (bottom - top)) for f, o in zip(fr, off)]
    left = [(x - w / 2, y) for x, y in pts]
    right = [(x + w / 2, y) for x, y in reversed(pts)]
    return "M" + " L".join(f"{num(x)} {num(y)}" for x, y in left + right) + "Z"


def c3(ctx):
    t = ctx.t
    G = word()
    bx = ink(G)
    rest = [g for g in G if g["ch"] != "l"]
    l = next(g for g in G if g["ch"] == "l")
    x0, y0, x1, y1 = l["b"]
    body = paths(rest, t["ink"]) + f'<path fill="{t["rust"]}" d="{crack((x0 + x1) / 2, y0, y1, x1 - x0)}"/>'
    return body, bx


def i3(ctx):
    return tile("navy") + f'<path fill="{ICON["peach"]}" d="{crack(32, 10, 54, 11, amp=2.3)}"/>'


def located_d(t_ink, t_blue, gx, s=1.0, oy=0.0, small=False):
    """The 'd' with its bowl replaced by a locating ring. gx = glyph origin, s = size/100."""
    stem = f'<rect x="{num(gx + 44.2 * s)}" y="{num(oy - 70 * s)}" width="{num(12.6 * s)}" height="{num(70 * s)}" fill="{t_ink}"/>'
    r_out, sw = 25.6 * s, 11 * s
    cx, cy = gx + 4.6 * s + r_out, oy - 24.8 * s
    ring = (f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(r_out - sw / 2)}" fill="none" stroke="{t_blue}" stroke-width="{num(sw)}"/>'
            f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num((6 if small else 4.8) * s)}" fill="{t_blue}"/>')
    return ring + stem


def c4(ctx):
    t = ctx.t
    G = word()
    bx = ink(G)
    d = G[0]
    return paths(G[1:], t["ink"]) + located_d(t["ink"], t["blue"], d["x"], small=ctx.small), bx


def i4(ctx):
    s = 0.62
    w, h = 56.8 * s - 4.6 * s, 71.4 * s
    gx = 32 - w / 2 - 4.6 * s
    return tile("card") + located_d(ICON["ink"], ICON["blue"], gx, s, oy=32 + h / 2 - 1.4 * s)


def dot_cloud(glyph, spacing, r, color, seed, jitter=0.32, opacity=1.0):
    f = FONTS[glyph["font"]]
    s = glyph["size"] / 1000
    b = f.tt.getGlyphSet()[glyph["name"]]
    bp = BoundsPen(f.gs)
    b.draw(bp)
    fx0, fy0, fx1, fy1 = bp.bounds
    rnd = random.Random(seed)
    out = []
    fy = fy0 + spacing / 2
    row = 0
    while fy < fy1:
        fx = fx0 + (spacing / 2 if row % 2 else spacing / 4)
        while fx < fx1:
            px = fx + rnd.uniform(-jitter, jitter) * spacing
            py = fy + rnd.uniform(-jitter, jitter) * spacing
            if f.inside(glyph["name"], px, py):
                out.append((glyph["x"] + px * s, glyph["y"] - py * s))
            fx += spacing
        fy += spacing * 0.87
        row += 1
    op = f' fill-opacity="{opacity}"' if opacity != 1 else ""
    return f'<g fill="{color}"{op}>' + "".join(
        f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(r)}"/>' for x, y in out) + "</g>"


def c5(ctx):
    t = ctx.t
    G = word()
    bx = ink(G)
    if ctx.small:
        return paths(G[:4], t["blue"]) + paths(G[4:], t["ink"]), bx
    params = [(54, 2.5), (48, 2.85), (43, 3.15), (38, 3.45)]
    body = "".join(dot_cloud(g, sp, r, t["blue"], seed=i + 7) for i, (g, (sp, r)) in enumerate(zip(G[:4], params)))
    rnd = random.Random(3)
    strays = "".join(
        f'<circle cx="{num(rnd.uniform(-14, 30))}" cy="{num(rnd.uniform(-86, 14))}" r="{num(rnd.uniform(1.3, 2))}"/>'
        for _ in range(7))
    body += f'<g fill="{t["blue"]}" fill-opacity="0.45">{strays}</g>' + paths(G[4:], t["ink"])
    return body, union(bx, (-16, -88, 0, 16))


def i5(ctx):
    G = word(x=0, y=0, size=72, text="d")
    g = G[0]
    b = g["b"]
    g["x"] += 32 - (b[0] + b[2]) / 2
    g["y"] = 32 + (b[3] - b[1]) / 2 - b[3]
    return tile("navy") + dot_cloud(g, 84, 2.5, ICON["paper"], seed=5, jitter=0.16)


def c6(ctx):
    t = ctx.t
    G = word()
    x0, y0, x1, y1 = ink(G)
    sw = 8 if ctx.small else 5
    px0, px1, top, bot = x0 - 30, x1 + 30, -90, 38
    r = (bot - top) / 2
    my = (top + bot) / 2
    lead = 58
    dr, hr = (12, 22) if ctx.small else (10, 19)
    body = (f'<rect x="{px0}" y="{top}" width="{num(px1 - px0)}" height="{bot - top}" rx="{r}" fill="none" '
            f'stroke="{t["blue"]}" stroke-width="{sw}"/>'
            f'<line x1="{num(px1)}" y1="{my}" x2="{num(px1 + lead)}" y2="{my}" stroke="{t["blue"]}" stroke-width="{sw}"/>'
            f'<circle cx="{num(px1 + lead + hr - dr)}" cy="{my}" r="{hr}" fill="{t["blue"]}" fill-opacity="0.22"/>'
            f'<circle cx="{num(px1 + lead + hr - dr)}" cy="{my}" r="{dr}" fill="{t["blue"]}"/>' + paths(G, t["ink"]))
    return body, (px0 - sw / 2, top - sw / 2, px1 + lead + 2 * hr - dr, bot + sw / 2)


def i6(ctx):
    b = ICON["blue"]
    return (tile("card") + f'<rect x="9" y="22" width="26" height="20" rx="10" fill="none" stroke="{b}" stroke-width="4"/>'
            f'<line x1="35" y1="32" x2="44" y2="32" stroke="{b}" stroke-width="4"/>'
            f'<circle cx="48" cy="32" r="9" fill="{b}" fill-opacity="0.22"/><circle cx="48" cy="32" r="5" fill="{b}"/>')


def corners(x0, y0, x1, y1, arm, color, sw):
    d = (f"M{num(x0)} {num(y0 + arm)}V{num(y0)}H{num(x0 + arm)}M{num(x1 - arm)} {num(y0)}H{num(x1)}V{num(y0 + arm)}"
         f"M{num(x1)} {num(y1 - arm)}V{num(y1)}H{num(x1 - arm)}M{num(x0 + arm)} {num(y1)}H{num(x0)}V{num(y1 - arm)}")
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="square"/>'


def c7(ctx):
    t = ctx.t
    G = word(font="sg600")
    x0, y0, x1, y1 = ink(G)
    sw = 9 if ctx.small else 6
    bx = (x0 - 26, y0 - 20, x1 + 26, y1 + 18)
    return paths(G, t["ink"]) + corners(*bx, 26, t["blue"], sw), (bx[0] - sw, bx[1] - sw, bx[2] + sw, bx[3] + sw)


def i7(ctx):
    return (tile("blue") + corners(13, 13, 51, 51, 10, ICON["card"], 4)
            + gauss_ellipse(ctx, 32, 32, 15, 9, -28, ICON["card"])
            + f'<ellipse cx="32" cy="32" rx="6.5" ry="4" transform="rotate(-28 32 32)" fill="{ICON["card"]}"/>')


def scan_trail(ctx, x, top, bottom, width, color, peak):
    i = ctx.add_def(f'<linearGradient id="{{id}}" x1="0" x2="1"><stop offset="0" stop-color="{color}" stop-opacity="0"/>'
                    f'<stop offset="1" stop-color="{color}" stop-opacity="{peak}"/></linearGradient>')
    return f'<rect x="{num(x - width)}" y="{num(top)}" width="{num(width)}" height="{num(bottom - top)}" fill="url(#{i})"/>'


def c8(ctx):
    t = ctx.t
    gap = 22
    G = word()
    G = G[:4] + word(x=G[4]["x"] + gap, text="splat")
    x0, y0, x1, y1 = ink(G)
    bx = (G[3]["b"][2] + G[4]["b"][0]) / 2
    bw = 8 if ctx.small else 5
    top, bot = -86, 32
    body = (scan_trail(ctx, bx, top, bot, 120, t["blue"], 0.28 if ctx.small else 0.22) + paths(G, t["ink"])
            + f'<rect x="{num(bx - bw / 2)}" y="{top}" width="{bw}" height="{bot - top}" rx="{bw / 2}" fill="{t["blue"]}"/>')
    return body, (x0, top, x1, bot)


def i8(ctx):
    d, b = glyph_in("sg700", "d", 64, 28, 33)
    bx = b[2] + 6
    return (tile("navy") + scan_trail(ctx, bx, 9, 55, 34, ICON["sky"], 0.45) + f'<path fill="{ICON["paper"]}" d="{d}"/>'
            f'<rect x="{num(bx - 2)}" y="9" width="4" height="46" rx="2" fill="{ICON["sky"]}"/>')


def c9(ctx):
    t = ctx.t
    G = word()
    x0, y0, x1, y1 = ink(G)
    ly = 42
    sw, dr, er, hr = (6, 10, 11, 20) if ctx.small else (3.5, 7, 8, 15)
    p = G[3]
    pm = (p["b"][0] + p["b"][2]) / 2
    nodes = [(x0 + dr, t["blue"], dr), (pm, t["blue"], dr)]
    body = (f'<line x1="{num(x0 + dr)}" y1="{ly}" x2="{num(x1 - er)}" y2="{ly}" stroke="{t["blue"]}" stroke-width="{sw}"/>'
            + "".join(f'<circle cx="{num(x)}" cy="{ly}" r="{r}" fill="{c}"/>' for x, c, r in nodes)
            + f'<circle cx="{num(x1 - er)}" cy="{ly}" r="{hr}" fill="{t["rust"]}" fill-opacity="0.22"/>'
            + f'<circle cx="{num(x1 - er)}" cy="{ly}" r="{er}" fill="{t["rust"]}"/>' + paths(G, t["ink"]))
    return body, (x0, y0, x1 - er + hr, ly + hr)


def i9(ctx):
    d, b = glyph_in("sg700", "d", 50, 32, 26)
    ly = 49
    return (tile("navy") + f'<path fill="{ICON["paper"]}" d="{d}"/>'
            f'<line x1="14" y1="{ly}" x2="50" y2="{ly}" stroke="{ICON["sky"]}" stroke-width="2.5"/>'
            f'<circle cx="14" cy="{ly}" r="3.6" fill="{ICON["sky"]}"/><circle cx="32" cy="{ly}" r="3.6" fill="{ICON["sky"]}"/>'
            f'<circle cx="50" cy="{ly}" r="7" fill="{ICON["peach"]}" fill-opacity="0.3"/><circle cx="50" cy="{ly}" r="4.2" fill="{ICON["peach"]}"/>')


def pin(ctx, cx, cy, R, tip, color, hole):
    """Map pin whose head carries a Gaussian splat. (cx, cy) = head centre, tip = y of the point."""
    h = tip - cy
    th = math.acos(R / h)
    tx, ty = R * math.sin(th), R * math.cos(th)
    d = (f"M{num(cx)} {num(tip)}L{num(cx - tx)} {num(cy + ty)}"
         f"A{num(R)} {num(R)} 0 1 1 {num(cx + tx)} {num(cy + ty)}Z")
    return (f'<path d="{d}" fill="{color}"/>'
            + gauss_ellipse(ctx, cx, cy, R * .68, R * .4, -28, hole)
            + f'<ellipse cx="{num(cx)}" cy="{num(cy)}" rx="{num(R * .36)}" ry="{num(R * .21)}" '
              f'transform="rotate(-28 {num(cx)} {num(cy)})" fill="{hole}"/>')


def c10(ctx):
    t = ctx.t
    G = word()
    bx = ink(G)
    R, cy, tip = 27, -43, 12
    cx = bx[0] - 18 - R
    hole = t["bg"]
    return pin(ctx, cx, cy, R, tip, t["blue"], hole) + paths(G, t["ink"]), union(bx, (cx - R, cy - R, cx + R, tip))


def i10(ctx):
    return tile("card") + pin(ctx, 32, 26, 17, 55, ICON["blue"], ICON["card"])


CONCEPTS = [
    (0, "Current placeholder", "For reference: today's soft blob in front of the wordmark.", "Symbol + wordmark", c0, i0),
    (1, "Splat cluster", "Three anisotropic Gaussians, the primitive a splat is literally made of, grouped into one soft mark.", "Symbol + wordmark", c1, i1),
    (2, "Covariance reticle", "A Gaussian's covariance ellipse and its axes double as a crosshair; the rust dot is the finding it locks onto.", "Symbol + wordmark", c2, i2),
    (3, "Crack l", "Pure type. The l of splat is a crack in rust, the colour the product uses for open findings.", "Pure type", c3, i3),
    (4, "Located d", "Pure type. The bowl of the d becomes the blue locating ring from the camera view: every record has a spot.", "Pure type", c4, i4),
    (5, "Points to splat", "deep is still a point cloud that densifies letter by letter until splat is solid: scan in, twin out.", "Pure type", c5, i5),
    (6, "Callout", "The wordmark sits in the product's own callout pill, with a leader line to a pinned spot.", "Wordmark + device", c6, i6),
    (7, "Viewfinder", "Camera corner brackets frame the name: capture is the entry point to everything.", "Wordmark + device", c7, i7),
    (8, "Scan pass", "A scan bar sweeps through the name and leaves a soft blue trail: the twin is refreshed with every pass.", "Wordmark + device", c8, i8),
    (9, "Timeline", "The name rests on the compare-over-time bar: two past records in blue, the newest open one in rust.", "Wordmark + device", c9, i9),
    (10, "Splat pin", "A location pin whose head holds a Gaussian splat: damage pinned to its exact 3D spot.", "Symbol + wordmark", c10, i10),
]

SMALL_NOTES = {
    5: "Below about 32 px the point cloud gives way to a solid two-tone version.",
}


def render(fn, theme, small=False, height=None):
    ctx = Ctx(theme, small)
    body, box = fn(ctx)
    return svg(ctx, body, box, height=height)


def render_icon(fn):
    ctx = Ctx(LIGHT)
    return icon_svg(ctx, fn(ctx))


# ---------------------------------------------------------------- page
CSS = """
:root{
  --paper:#F1EEE6;--paper-2:#E8E2D4;--card:#FBFAF7;--ink:#172033;--ink-2:#4A5163;--muted:#7D786A;
  --line:#E2DCCF;--line-2:#D3CBBB;--blue:#2453D4;--blue-soft:#E3E9FB;--rust:#C24A1C;--rust-soft:#F7E5DB;
  --green:#2F7D4B;--green-soft:#E1EFE3;--vp:#1A2130;--vp-2:#232C3E;--vp-line:#36435C;--vp-ink:#E9ECF3;--vp-muted:#9AA4B8;
  --r-card:22px;--r-md:14px;--r-sm:9px;
  --display:"Space Grotesk",system-ui,sans-serif;--body:"IBM Plex Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace;
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 var(--body);-webkit-font-smoothing:antialiased}
.wrap{max-width:1240px;margin:0 auto;padding:56px 32px 96px}
.eyebrow{font:600 11px/1 var(--display);letter-spacing:.14em;text-transform:uppercase;color:var(--blue);margin:0 0 14px}
h1{font:700 44px/1.08 var(--display);letter-spacing:-.025em;margin:0 0 14px}
.lede{color:var(--ink-2);max-width:720px;margin:0}
.lede code,.mono{font-family:var(--mono);font-size:.92em}
.overview{margin:40px 0 56px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
.ov-panel{border-radius:var(--r-card);padding:22px 24px;display:grid;grid-template-columns:repeat(2,1fr);gap:18px 28px;align-items:center}
.ov-panel.light{background:var(--card);border:1px solid var(--line)}
.ov-panel.dark{background:var(--vp)}
.ov-item{display:flex;align-items:center;gap:12px;min-height:40px;text-decoration:none}
.ov-item .n{font:500 11px/1 var(--mono);color:var(--muted);width:18px;flex:none}
.ov-panel.dark .n{color:var(--vp-muted)}
.ov-item svg{height:30px;width:auto;max-width:100%}
.concept{background:var(--card);border:1px solid var(--line);border-radius:var(--r-card);padding:28px;margin:0 0 20px;scroll-margin-top:24px}
.concept.picked{border-color:var(--blue);box-shadow:0 0 0 3px var(--blue-soft)}
.c-head{display:flex;gap:20px;align-items:flex-start;margin-bottom:22px}
.c-num{font:600 40px/1 var(--display);letter-spacing:-.03em;color:var(--blue);min-width:62px}
.c-title{flex:1}
.c-title h2{font:600 24px/1.15 var(--display);letter-spacing:-.015em;margin:2px 0 6px}
.c-title p{margin:0;color:var(--ink-2);max-width:680px}
.tag{display:inline-block;font:500 12px/1 var(--body);padding:6px 10px;border-radius:999px;background:var(--paper);color:var(--ink-2);margin-left:8px;vertical-align:3px}
.pick{font:500 13px/1 var(--body);border:1px solid var(--line-2);background:var(--card);color:var(--ink);border-radius:999px;padding:10px 16px;cursor:pointer;white-space:nowrap}
.pick:hover{border-color:var(--ink-2)}
.concept.picked .pick{background:var(--blue);border-color:var(--blue);color:#fff}
.stage{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.panel{border-radius:var(--r-md);min-height:200px;display:flex;align-items:center;justify-content:center;padding:40px 32px;position:relative}
.panel.light{background:var(--paper)}
.panel.dark{background:var(--vp)}
.panel svg{height:68px;width:auto;max-width:100%}
.panel .lbl{position:absolute;left:16px;top:14px;font:500 11px/1 var(--mono);color:var(--muted)}
.panel.dark .lbl{color:var(--vp-muted)}
.tests{display:grid;grid-template-columns:auto auto 1fr;gap:14px;margin-top:14px}
.test{border-radius:var(--r-md);background:var(--paper);padding:14px 16px}
.test.dark{background:var(--vp)}
.test h3{font:600 11px/1 var(--display);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 12px}
.test.dark h3{color:var(--vp-muted)}
.icons{display:flex;align-items:flex-end;gap:14px}
.icons svg{display:block;border-radius:22%}
.test.dark .icons svg{box-shadow:0 0 0 1px #4A5873}
.smalls{display:flex;flex-direction:column;gap:10px}
.smalls svg{display:block;height:20px;width:auto}
.nav{display:flex;align-items:center;gap:18px;background:var(--card);border:1px solid var(--line);border-radius:var(--r-md);padding:12px 16px}
.nav svg{height:20px;width:auto;flex:none}
.nav .pills{display:flex;gap:4px;background:var(--paper);border-radius:999px;padding:4px;margin-left:auto}
.nav .pills span{font:500 12px/1 var(--body);padding:7px 12px;border-radius:999px;color:var(--ink-2)}
.nav .pills span:first-child{background:var(--ink);color:#fff}
.nav-dark{background:var(--vp-2);border-color:var(--vp-line);margin-top:8px}
.nav-dark .pills{background:var(--vp)}
.nav-dark .pills span{color:var(--vp-muted)}
.nav-dark .pills span:first-child{background:var(--vp-ink);color:var(--vp)}
.note{font-size:13px;color:var(--muted);margin:10px 0 0}
.dl{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.dl button{font:500 12px/1 var(--body);border:1px solid var(--line-2);background:transparent;color:var(--ink-2);border-radius:999px;padding:8px 12px;cursor:pointer}
.dl button:hover{color:var(--ink);border-color:var(--ink-2)}
.picks{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--paper) 92%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);}
.picks .wrap{padding:12px 32px;display:flex;align-items:center;gap:12px}
.picks b{font:600 13px/1 var(--display)}
.picks span{font:500 13px/1 var(--mono);color:var(--ink-2)}
.picks button{margin-left:auto;font:500 12px/1 var(--body);border:1px solid var(--line-2);background:var(--card);border-radius:999px;padding:8px 12px;cursor:pointer}
footer{color:var(--muted);font-size:13px;margin-top:40px}
@media (max-width:900px){
  .overview,.stage{grid-template-columns:1fr}
  .tests{grid-template-columns:1fr}
  .wrap{padding:32px 16px 64px}
  .picks .wrap{padding:10px 16px}
  h1{font-size:34px}
  .c-head{flex-wrap:wrap;justify-content:space-between}
  .c-title{order:3;flex-basis:100%}
  .tests>*{min-width:0}
  .nav .pills{display:none}
  .concept{padding:20px}
  .panel{padding:32px 20px;min-height:150px}
  .ov-panel{grid-template-columns:1fr}
}
"""

JS = """
const KEY='deepsplat-logo-picks';
let picks=[];try{picks=JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){}
function sync(){
  document.querySelectorAll('.concept').forEach(c=>{
    const on=picks.includes(+c.dataset.n);c.classList.toggle('picked',on);
    c.querySelector('.pick').textContent=on?'Picked':'Pick';
  });
  document.getElementById('picklist').textContent=picks.length?picks.slice().sort((a,b)=>a-b).join(', '):'none yet';
  try{localStorage.setItem(KEY,JSON.stringify(picks))}catch(e){}
}
document.querySelectorAll('.pick').forEach(b=>b.addEventListener('click',()=>{
  const n=+b.closest('.concept').dataset.n;picks=picks.includes(n)?picks.filter(x=>x!==n):[...picks,n];sync();
}));
document.getElementById('clear').addEventListener('click',()=>{picks=[];sync()});
document.querySelectorAll('.dl button').forEach(b=>b.addEventListener('click',()=>{
  const src=document.getElementById(b.dataset.src).querySelector('svg').cloneNode(true);
  src.removeAttribute('height');src.removeAttribute('width');
  const blob=new Blob(['<?xml version="1.0" encoding="UTF-8"?>\\n'+src.outerHTML],{type:'image/svg+xml'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=b.dataset.file;a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}));
sync();
"""


def concept_card(n, name, why, kind, fn, ifn):
    light = render(fn, LIGHT)
    dark = render(fn, DARK)
    sl = render(fn, LIGHT, small=True)
    sd = render(fn, DARK, small=True)

    def icons():
        return "".join(render_icon(ifn).replace("<svg ", f'<svg width="{s}" height="{s}" ', 1) for s in (64, 32, 16))
    slug = name.lower().replace(" ", "-")
    note = f'<p class="note">{SMALL_NOTES[n]}</p>' if n in SMALL_NOTES else ""
    return f"""
<section class="concept" id="c{n}" data-n="{n}">
  <div class="c-head">
    <div class="c-num">{n:02d}</div>
    <div class="c-title"><h2>{html.escape(name)}<span class="tag">{kind}</span></h2><p>{html.escape(why)}</p></div>
    <button class="pick" type="button">Pick</button>
  </div>
  <div class="stage">
    <div class="panel light" id="c{n}-light"><span class="lbl">paper</span>{light}</div>
    <div class="panel dark" id="c{n}-dark"><span class="lbl">viewport</span>{dark}</div>
  </div>
  <div class="tests">
    <div class="test"><h3>App icon · 64 / 32 / 16</h3><div class="icons" id="c{n}-icon">{icons()}</div></div>
    <div class="test dark"><h3>On dark</h3><div class="icons">{icons()}</div></div>
    <div class="test"><h3>20 px high</h3>
      <div class="nav"><span id="c{n}-small">{sl}</span><div class="pills"><span>Sites</span><span>Issues</span><span>Reports</span></div></div>
      <div class="nav nav-dark"><span id="c{n}-small-dark">{sd}</span><div class="pills"><span>Sites</span><span>Issues</span><span>Reports</span></div></div>
      {note}
    </div>
  </div>
  <div class="dl">
    <button type="button" data-src="c{n}-light" data-file="deepsplat-{n:02d}-{slug}-light.svg">SVG · light</button>
    <button type="button" data-src="c{n}-dark" data-file="deepsplat-{n:02d}-{slug}-dark.svg">SVG · dark</button>
    <button type="button" data-src="c{n}-icon" data-file="deepsplat-{n:02d}-{slug}-icon.svg">SVG · icon</button>
  </div>
</section>"""


def page():
    ov_l = "".join(f'<a class="ov-item" href="#c{n}"><span class="n">{n:02d}</span>{render(fn, LIGHT, small=True)}</a>'
                   for n, _, _, _, fn, _ in CONCEPTS)
    ov_d = "".join(f'<a class="ov-item" href="#c{n}"><span class="n">{n:02d}</span>{render(fn, DARK, small=True)}</a>'
                   for n, _, _, _, fn, _ in CONCEPTS)
    cards = "".join(concept_card(*c) for c in CONCEPTS)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>deepsplat logo ideas</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="picks"><div class="wrap"><b>Your picks</b><span id="picklist">none yet</span><button id="clear" type="button">Clear</button></div></div>
<main class="wrap">
  <p class="eyebrow">Brand · logo ideas</p>
  <h1>Ten directions for deepsplat</h1>
  <p class="lede">Every wordmark is lowercase Space Grotesk, outlined to vector paths, so any of these can be exported straight to
  <code>design/brand/logos/</code>. Each concept shows paper and viewport backgrounds, an app icon at 64, 32 and 16 px,
  and the lockup at 20 px inside a header bar. Tell me the numbers you like, for example “3 and 7”.</p>
  <div class="overview">
    <div class="ov-panel light">{ov_l}</div>
    <div class="ov-panel dark">{ov_d}</div>
  </div>
  {cards}
  <footer>Generated by <span class="mono">design/brand/tools/build_logo_ideas.py</span> · Space Grotesk and IBM Plex are licensed under the SIL Open Font License.</footer>
</main>
<script>{JS}</script>
</body>
</html>
"""


def ensure_fonts(d):
    d.mkdir(parents=True, exist_ok=True)
    for key, rel in FONT_FILES.items():
        p = d / pathlib.Path(rel).name
        if not p.exists():
            urllib.request.urlretrieve(CDN + rel, p)
        FONTS[key] = Font(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fonts", type=pathlib.Path, default=HERE / ".fonts")
    ap.add_argument("--out", type=pathlib.Path, default=HERE.parent / "logo-ideas.html")
    a = ap.parse_args()
    ensure_fonts(a.fonts)
    a.out.write_text(page(), encoding="utf-8")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
