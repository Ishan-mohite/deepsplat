# deepsplat design system

Rules for building deepsplat UI, documents and slides. The visual overview is in the [README](README.md#design-system).
Tokens live in [`design/system/tokens.css`](design/system/tokens.css) and [`tokens.json`](design/system/tokens.json),
ready-made components in [`design/system/components.css`](design/system/components.css), and logo files in
[`design/brand/logos/`](design/brand/logos/).

If a value is not in the tokens, it is not part of the system. Add it to the tokens first, then use it.

## Use it in another branch

```bash
git fetch origin design-system
git checkout origin/design-system -- DESIGN.md design/system design/brand/logos
```

Then load the fonts and the CSS:

```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="design/system/tokens.css">
<link rel="stylesheet" href="design/system/components.css">
```

For native apps or other tools, read `tokens.json` (W3C design tokens format).

## Name

| Where | How |
|---|---|
| Logo | The wordmark from the logo files. Never retype it. |
| Running text, titles, UI copy | **deepsplat**: lowercase and one word, also at the start of a sentence and in headings. Never "DeepSplat", "Deep Splat", "Deepsplat" or "DEEPSPLAT". |
| Code, URLs, package and file names | `deepsplat` |

## Logo

| File | Use |
|---|---|
| `deepsplat-wordmark-light.svg` | Main logo on paper, card or white. "deep" is a blue point cloud that becomes the solid ink "splat". |
| `deepsplat-wordmark-dark.svg` | Main logo on the dark viewport or ink surfaces. |
| `deepsplat-wordmark-small-light.svg`, `-small-dark.svg` | Below 32 px logo height. Points cannot be resolved there, so "deep" is solid blue. |
| `deepsplat-icon.svg` | App icon, avatar, any square slot from 24 px up. A beam from the camera hits the wall; the hit glows in rust. |
| `deepsplat-icon-16.svg` | Favicon and anything at 16 to 24 px: same drawing with heavier strokes. |

- **Size:** Use the point-cloud wordmark at 32 px logo height or more, which is about 140 px wide. Below that, use the small wordmark. Below 16 px, use the icon only.
- **Clear space:** Keep at least the height of the letter e free on every side.
- **Background:** Only paper, card, white, the viewport colour or ink. Never put the logo on blue, rust or photos without a solid panel behind it.
- **Do not:** recolour the points, rebuild the cloud, set the word in another font, capitalise it, stretch it, add shadows or outlines, or put the icon and the wordmark in one lockup at app-icon sizes.
- **Icon on dark surfaces:** The icon tile is navy (`#172033`) and almost disappears on the viewport colour. On dark surfaces, give it a 1 px `--vp-line` outline or place it on `--vp-2`.

## Colour

The product UI is light: warm paper, navy ink, one blue and rust for open findings. Only the 3D viewport is dark.
Every colour has one job. Do not use a colour for decoration.

| Token | Hex | Role |
|---|---|---|
| `--paper` | `#F1EEE6` | Page background |
| `--paper-2` | `#E8E2D4` | Sunken panels, hero areas, inactive tracks |
| `--card` | `#FBFAF7` | Cards, sheets, inputs |
| `--ink` | `#172033` | Text, dark buttons, selected navigation |
| `--ink-2` | `#4A5163` | Secondary text and metadata |
| `--muted` | `#7D786A` | Hints, axis labels, placeholders |
| `--line`, `--line-2` | `#E2DCCF`, `#D3CBBB` | Card borders and dividers; control borders |
| `--blue`, `--blue-soft` | `#2453D4`, `#E3E9FB` | Primary action, links, eyebrows, "located" and "closed"; selected rows |
| `--rust`, `--rust-soft` | `#C24A1C`, `#F7E5DB` | Open findings, worsening issues, the newest record that needs action |
| `--green`, `--green-soft` | `#2F7D4B`, `#E1EFE3` | Live and done: "Scan active", saved, repaired |
| `--amber`, `--amber-soft` | `#946300`, `#F6ECD2` | Warnings that are not findings, such as weak tracking or low battery |

Rules:

- Use one primary (blue) button per view.
- Rust always means "needs attention". A rust number, pin or bar must point to something open.
- Never show a state by colour alone. Chips carry a glyph and a label, pins carry a callout or a list entry.

Contrast (WCAG 2.1):

| Pair | Ratio | Use |
|---|---|---|
| ink on paper / card | 14.0 / 15.6 | All text |
| ink-2 on paper / card | 6.8 / 7.6 | All text |
| blue on paper / card | 5.6 / 6.2 | Text and links |
| white on blue | 6.2 | Button labels |
| rust on card | 4.7 | Text |
| rust on paper | 4.2 | 600 weight or 18 px and up |
| green on green-soft, amber on amber-soft, rust on rust-soft | 4.3, 4.4, 4.0 | Chip labels at 12 px / 600 next to a glyph; not for running text |
| muted on paper / card | 3.8 / 4.2 | Hints and axis labels only, never essential text |
| severity low, medium, high on card | 3.2, 2.9, 3.7 | Dots, bars and fills only, the label stays ink |

## Typography

| Token | Font | Size / line height | Weight | Tracking | Use |
|---|---|---|---|---|---|
| `display-xl` | Space Grotesk | 44 / 1.08 | 700 | -0.025em | Page and slide titles |
| `display-l` | Space Grotesk | 32 / 1.1 | 700 | -0.02em | Screen titles ("Unit 3.04") |
| `display-m` | Space Grotesk | 24 / 1.15 | 600 | -0.015em | Section titles |
| `display-s` | Space Grotesk | 18 / 1.25 | 600 | -0.01em | Card titles |
| `kpi` | Space Grotesk | 40 / 1 | 600 | -0.03em | Big numbers |
| `body` | IBM Plex Sans | 15 / 1.5 | 400 | 0 | Running text |
| `body-s` | IBM Plex Sans | 13 / 1.45 | 400 | 0 | Metadata, captions |
| `label`, `button` | IBM Plex Sans | 13 / 1.2, 14 / 1 | 500, 600 | 0 | Controls, chips, buttons |
| `eyebrow` | Space Grotesk | 11 / 1 | 600 | 0.14em | Uppercase label above a title, in blue |
| `mono`, `mono-s` | IBM Plex Mono | 13 / 1.4, 11 / 1.3 | 400, 500 | 0 | IDs, timestamps, coordinates, axis ticks |

- Headings and buttons use sentence case. Only eyebrows are uppercase.
- Use `·` (middle dot) to join parts of a label: "Crack · Bath, north wall".
- Use mono for anything a person might compare or copy: `REC-0148`, `14 Mar 2026, 10:42`, `1.4 m`.

## Spacing, radii, elevation

- **Spacing:** 4 px base: `--space-1` 4, `-2` 8, `-3` 12, `-4` 16, `-5` 20, `-6` 24, `-8` 32, `-10` 40, `-14` 56, `-18` 72. Inside cards use 20 to 24 px; between cards use 14 to 16 px.
- **Radii:** cards and tiles `--r-card` 22 px, panels and the viewport `--r-md` 14 px, thumbnails and small boxes `--r-sm` 9 px, buttons, chips, callouts and inputs `--r-pill` 999 px.
- **Elevation:** The UI is flat; separate surfaces with colour and a 1 px `--line` border. Use `--shadow-float` only for menus, popovers and sheets over content. Focus is always visible as `--focus-ring`.

## Components

All classes come from `components.css`.

| Component | Markup | Notes |
|---|---|---|
| Card | `<div class="ds-card">` | `--card` fill, 1 px `--line`, 22 px radius. `ds-card--sunken` for areas inside a card, `ds-card--hero` for the main panel of a screen. |
| Button | `<button class="ds-btn ds-btn--primary">Record defect</button>` | Pills, 44 px high (`ds-btn--sm` 36 px). Variants: `--primary` (blue, one per view), `--dark` (ink, a second strong action such as "Open 3D scan"), `--secondary` (outline), `--ghost` (text). |
| Navigation | `<nav class="ds-nav"><a aria-current="page">Sites</a><a>Issues</a></nav>` | Segmented pills; the current item is ink with white text. |
| Search | `<input class="ds-input" placeholder="Search sites">` | Pill on `--paper`. |
| Chip | `<span class="ds-chip">Mould</span>` | `--live` for "Scan active" (green with a dot), `--selected` for a chosen filter or type, `--info` for context such as "Localized · Bathroom". |
| Severity | `<span class="ds-sev" data-sev="high">High</span>` | A diamond in the severity colour plus the label: low, medium, high, critical. |
| Issue status | `<span class="ds-status" data-status="worsening">Worsening</span>` | Glyph plus label on a soft pill: new `+` blue, worsening `↗` rust, stable `=` grey, repaired `✓` green. |
| Callout pill | `<span class="ds-callout ds-callout--open">Crack · north wall · open</span>` | Outline pill on `--card` that labels a pin. Blue by default, rust when the finding is open. Draw the leader line as an SVG line with class `ds-leader`, 1.5 px, in the same colour, ending at the pin. |
| Pin | `<span class="ds-pin ds-pin--open is-selected"></span>` | 14 px dot with a 2 px ring in the surface colour. Rust for open, blue for everything else. `is-selected` adds a soft halo. |
| Time bar | `<div class="ds-timebar"><span class="ds-timebar__node">2023</span>…<span class="ds-timebar__node ds-timebar__node--open">2026</span></div>` | Records at one spot over time on a 2 px blue line. Past records are blue, the newest open one is rust. Years in mono. |
| KPI tile | `<div class="ds-kpi"><span class="ds-kpi__label">Open findings</span><span class="ds-kpi__value ds-kpi__value--open">3</span></div>` | Value in ink. Use `--open` (rust) only for counts of open findings and `--time` (blue) for durations. |
| Record row | `<div class="ds-row"><img class="ds-row__thumb"><div><div class="ds-row__title">Crack · Bath, north wall</div><div class="ds-row__meta">Move-out · today 10:42 · M. Keller</div></div></div>` | Thumbnail, title, metadata. `ds-row--boxed` for tappable rows in lists. |

## 3D viewport

- The viewport is always dark (`--vp`), also inside the light UI. Use `<div class="ds-viewport">`.
- Panels and toolbars over the viewport use `--vp-2` with a `--vp-line` border, text in `--vp-ink`, secondary text in `--vp-muted`.
- On dark, the blue role becomes `--pick` (`#7EA2FF`) and the rust role becomes `--vp-rust` (`#E8784A`).
- **Pick reticle:** a 2 px `--pick` ring with four short ticks, plus a centre dot. It marks the spot the camera or cursor points at.
- **Pins:** open findings in `--vp-rust`, everything else in `--pick`, each with a 2 px `--vp` ring.
- **Segmentation (SAM 3 overlay):** give each class a fixed colour by its index, `--seg-1` to `--seg-7`, in that order, and repeat after 7. Unlabelled and background areas use `--seg-neutral`. Fill masks at about 35 % opacity, draw outlines at 2 px in full colour, and show the class name on hover or in a legend.
- **Severity on dark:** critical (`#B3261E`) has only 2.5:1 contrast on the viewport, so always draw it with a white ring and a label.

## Data visualisation

- **Colour by role, not by series:** blue for the normal case, rust only for what needs action, green for repaired. Segmentation colours are only for segmentation classes.
- **Bars:** one series in blue. Highlight one bar in rust only if it needs attention, for example the current month with open findings. Round only the top corners (4 px). Put a 1 px `--line-2` baseline below the bars and no other gridlines unless values must be read off.
- **Status donut:** closed in blue, open in rust, the total in the middle in Space Grotesk, a legend with counts ("Open · 3"). Use a donut only for 2 or 3 parts; otherwise use bars.
- **Labels:** axis ticks in `mono-s` and `--muted`. Mark example numbers with "Sample data".
- **Avoid:** gradients, 3D charts, dual axes and colour as the only key.

## Voice

UI copy is in English, plain and in sentence case.

| Do | Don't |
|---|---|
| Record defect | Create new defect entry |
| Saved, hashed and timestamped | Success! Your record has been saved. |
| 2 earlier records here | There are 2 previous records at this location |
| Crack · Bath, north wall | CRACK – Bathroom (North Wall) |
| Position: 1.4 m above floor | 6-DoF pose: [0.12, 1.40, -2.03] |

- Lead with the verb on buttons: "Save record", "Export PDF", "Open 3D scan".
- Say what happened, not that it worked: "Filled in automatically", "Repaired in 6 days".
- Name damage plainly: crack, mould, stain, scratch, corrosion. Severity is low, medium, high or critical. Status is new, worsening, stable or repaired.
- Write dates as `14 Mar 2026, 10:42` and units with a space: `1.4 m`, `6 days`.
- Do not use exclamation marks, and do not show technical terms such as "6-DoF pose" or "splat" in the field UI.

## Files

| Path | Content |
|---|---|
| `DESIGN.md` | This document |
| `design/system/tokens.css`, `tokens.json` | Design tokens |
| `design/system/components.css` | Component classes (`ds-…`) |
| `design/system/img/` | Images used in the README, generated from `tokens.json` |
| `design/system/tools/build_docs_images.py` | Regenerates `img/` after a token change: `uv run design/system/tools/build_docs_images.py` |
| `design/brand/logos/` | Wordmarks and icons (SVG) |
| `design/ux-layouts/imoscan_product_visuals.pdf` | Layout reference for screens, cards and callouts |
