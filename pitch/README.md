# deepsplat jury pitch

4 minutes · Track 2, Resilient & Safe City · "3D damage map: scan a site and flag what has changed"

## Present it

Open `index.html` in Chrome (double-click works, no server or internet needed) and press `F` for fullscreen.

| Key | Does |
|---|---|
| `→` `Space` `PageDown` (clicker) or a click | next build or slide |
| `←` `PageUp` | back |
| `S` | speaker notes with the target time per slide |
| `T` / `R` | timer against 4:00 / reset it |
| `P` | pause or resume the demo video |
| `B` | black screen |
| `1` to `8` | jump to a slide |

The demo video on slide 4 starts by itself and moves on to slide 5 when it ends. Pressing next skips it.

Chrome is the tested browser. Without WebGL the point cloud is drawn on a plain canvas instead.

## Change the video

`assets/pipeline.mp4` is the Drift export (`~/Videos/Drift/deepsplat/deepsplat-pipeline.mp4`) cut at 57.9 s and
dissolved into its own blank first frame, so it begins and ends on the page background:

```bash
ffmpeg -y -i deepsplat-pipeline.mp4 -filter_complex "[0:v]trim=duration=57.9,setpts=PTS-STARTPTS,fps=30,settb=AVTB[a];[0:v]trim=end_frame=1,loop=loop=15:size=1:start=0,setpts=N/30/TB,fps=30,settb=AVTB[b];[a][b]xfade=transition=fade:duration=0.3:offset=57.6,format=yuv420p[v]" -map "[v]" -c:v libx264 -crf 16 -preset slow -an -movflags +faststart pitch/assets/pipeline.mp4
```


## Where the numbers come from

| On the slide | Source |
|---|---|
| Crack · 0.55 m · new, ISS-0007, 15 Nov 2026 20:29 | the live viewer, shown in the video |
| 3 M Gaussians | viewer ("3,000,000 Gaussians") |
| 6 mm median positioning error, 0 wrong | `tools/eval_vps.py` on held-out views of the scan (commit cb7bcb7: 62 of 96 localized). Scan views, not phone photos |
| 10 s sweep + photo | the app recording in the video (10.5 s, 53 frames) |
| 51 automated tests passing | `uv run pytest`, run on 16 Nov |
| matched within 15 cm; worsening at +20 % and +2 cm; repaired after a person confirms | `inspection/issues.py`, `backend/pipeline.py` |
| 39.8 m² drivable | `maps/auditorium/summary.json` |

The point cloud on slides 1, 3 and 7 is the real scan (`maps/auditorium/elevation_cloud.pcd`).
