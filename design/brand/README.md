# deepsplat brand

Logo exploration for the lowercase wordmark **deepsplat**. The UX reference for colours, surfaces and components is
[`../ux-layouts/imoscan_product_visuals.pdf`](../ux-layouts/imoscan_product_visuals.pdf).

| Path | What it is |
|---|---|
| `logo-ideas.html` | Concept sheet: numbered logo ideas, each on paper and viewport backgrounds, as an app icon (64/32/16 px) and at 20 px high in a header bar. Generated, do not edit by hand. |
| `tools/build_logo_ideas.py` | Generator for the sheet. All logos are inline SVG with the text outlined to paths. |

## Run it

Requires [uv](https://docs.astral.sh/uv/) (it installs `fonttools` and `uharfbuzz` from the script's inline metadata).

```bash
uv run design/brand/tools/build_logo_ideas.py
python3 -m http.server 8765 --bind 127.0.0.1 --directory design
```

Then open <http://localhost:8765/brand/logo-ideas.html>. On first run the script downloads Space Grotesk (SIL OFL) from
the `@fontsource` packages on jsDelivr into `tools/.fonts/` (git-ignored). In the Claude desktop app, the `design`
entry in `.claude/launch.json` starts the same server.

On the page, **Pick** marks favourites (kept in your browser only) and the **SVG** buttons download the light, dark or
icon version of a concept.

## Add or change a concept

Each concept is two functions in `tools/build_logo_ideas.py` plus one row in `CONCEPTS`:

- `cN(ctx)` returns `(svg_body, bounding_box)` for the lockup. Coordinates use a 100-unit font size with the baseline
  at `y = 0`, so the x-height is about 49 units, ascenders reach `y = -70` and descenders `y = +20`.
  `ctx.t` is the theme (`LIGHT` or `DARK`, with `ink`, `blue`, `rust`, `bg`, ...). `ctx.small` is true for the 20 px
  version; use it to thicken hairlines or simplify detail that disappears at that size.
- `iN(ctx)` returns the body of a 64×64 app icon. Start with `tile("blue" | "navy" | "card")` and use the fixed
  `ICON` colours, since the icon is the same on light and dark backgrounds.

Helpers:

| Helper | Use |
|---|---|
| `word(font, x, y, text=...)` | Shaped glyphs (HarfBuzz kerning, -0.03em tracking). Each glyph carries its path `d`, bounds `b` and character `ch`, so single letters can be replaced (see `c3`, `c4`). |
| `paths(glyphs, fill)`, `ink(glyphs)` | One `<path>` for a glyph list; its ink bounding box. |
| `glyph_in(font, ch, size, cx, cy)` | A single glyph centred on a point, for icons. |
| `gauss_ellipse(ctx, cx, cy, rx, ry, deg, color)` | Soft anisotropic Gaussian (a "splat") as a radial gradient. |
| `dot_cloud(glyph, spacing, r, color, seed)` | Fill a glyph outline with jittered points. |
| `corners`, `crack`, `pin`, `located_d`, `scan_trail` | Building blocks used by the existing concepts. |

Gradient and mask ids are generated per SVG instance via `ctx`, so never hard-code `id` attributes.

Rules that keep concepts comparable:

- The wordmark is always lowercase `deepsplat`, one word, Space Grotesk (700 by default).
- Use only palette colours: ink `#172033`, blue `#2453D4`, rust `#C24A1C` (open findings), and on the dark viewport
  `#E9ECF3`, `#7EA2FF` and `#E8784A`.
- Check every concept at 20 px and as a 16 px favicon before you propose it.

Rebuild after editing and reload the page (add `?v=2` to bypass the browser cache).
