# DeepSplat backend

Implements the "Inspect & log damage" pipeline from the project README, for the iOS capture app
(`docs/PLAN.md` on `mobile-recorder-app`), robots / glasses, and the imoscan app + dashboard for
property managers (`design/ux-layouts/imoscan_product_visuals.pdf` on `ux-design-layouts`).

```
capture ─► quality gate ─► detect (SAM 3) ─► localize in the splat ─► lift to 3D ─► match issues ─► store
                                             │
                                             ├─ client already localized (robot / glasses): camera_to_map
                                             ├─ VPS on 4–6 video frames, carried to the photo via ARKit poses
                                             └─ VPS on the photo itself
```

| Folder | Content (README structure) |
|---|---|
| `backend/` | FastAPI app, database models, storage, pipeline orchestration |
| `inspection/` | damage classes, SAM 3 detector, lifting onto the splat, severity, issue matching, quality gate |
| `localization/` | camera conventions of the app, frame selection, frame-pose alignment, own VPS (SIFT, CPU), hloc VPS |
| `tools/` | SAM 3 evaluation scripts (test set from Wikimedia Commons, prompt / threshold tuning) |
| `tests/` | unit + end-to-end tests (fake detector, rendered synthetic room for the VPS), ~30 s |

## Run locally

```bash
uv sync
cp backend/.env.example .env           # set ROBOFLOW_API_KEY, or DETECTOR=fake
uv run uvicorn backend.main:app --reload
# API docs: http://localhost:8000/docs
uv run pytest
```

## imoscan UX → API

| Screen / element | Endpoint / field |
|---|---|
| Unit overview: "Unit 3.04 · Property A · 3rd floor", floor plan, open findings | `GET /maps/{id}` (`name`, `property`, `floor_label`, `scanned_at`, `floor_plan_url`, `rooms`), `GET /maps/{id}/dashboard` → `open_list` (with `floor_plan_px` pins) |
| Camera: "Localized · Bathroom", "Crack detected", "2 earlier records here" | `POST /localize/multi` → `device_to_map`, `room`; `POST /detect`; `POST /maps/{id}/in-view` → open issues in the frame with pixel positions |
| Record: type chips "detected by an open model", recorder, time, "1.4 m above floor", "Earlier record here · linked" | `/v1/captures` or `POST /inspect` with `recorder`, `event_kind`, `handover_id`; result: `observations[].issue_id` + `change` (linked to an existing issue or new), issue `place`, `height_m`; `PATCH /issues/{id}` `{type}` to correct |
| Compare over time: timeline at this spot, move-in vs move-out | `GET /issues/{id}` → `observations[]` with `timestamp`, `event_kind`, `recorder`, `image_url`, `overlay_url` |
| "Saved, hashed and timestamped" | `original_sha256` (upload), per record `image_sha256`, `recorded_at`, `record_hash` + `prev_hash` (hash chain per unit), `GET /maps/{id}/verify` |
| Dashboard: open findings, closed this year, days to repair, records, by status, records per month, latest records | `GET /maps/{id}/dashboard` |
| Handovers: "Start handover" | `POST /maps/{id}/handovers` `{kind: move_in \| move_out, tenant, started_by}`, `POST /handovers/{id}/finish`, `GET /handovers/{id}` (records, new issues, new since previous handover) |
| "Export PDF", "Add to PDF report" | `GET /maps/{id}/report.pdf` (`?handover_id=` for one handover) |
| "Old defects leave the view once repaired" | issue lists default to open issues; `PATCH /issues/{id}` `{status: "repaired"}` |

Places: every issue gets `room` (rooms drawn as floor-plane polygons via `PATCH /maps/{id}`
`{rooms: [{name, polygon}]}`; damage on a wall lies on the boundary, so the nearest room within 30 cm
counts), `surface` (`floor`, `ceiling`, `north wall`, ... from the surface normal and the map's
`north`), `height_m` above the floor (`floor_z`, estimated from the splat), `floor_xy`/`floor_plan_px`.

## Web viewer (branch `web-viewer`)

The viewer prototype (`viewer/index.html`) runs on mock data from `MockApi`. The backend serves the
same shape, so switching is two calls:

| `MockApi` | Backend |
|---|---|
| `listSites()` → `[{id, option}]` | `GET /viewer/sites` |
| `getSite(id)` → site with `summary`, `monthly`, `issues` (README record), `captures` | `GET /viewer/sites/{id}` |

Differences to the mock:
* Geometry: instead of the procedural `segments` / `plan`, the site has `splat_url` (load with Spark)
  and `splat_to_viewer` (4x4, apply to the splat mesh), plus `floor_plan_url` / `floor_plan`.
* Positions, normals and `camera_pose` are already in the viewer frame (y up, three.js cameras).
* `types`: the backend's damage types in the viewer's `TYPES` format (merge it; `stain` → `leak`,
  `coating_damage` → `coating`; `hole` etc. are new keys).
* Captures: `stage` 0–5 follows the viewer's `PIPELINE`; failed captures carry `reason`
  (e.g. pose confidence below the threshold), done ones `result`.
* Extra fields the viewer can use: issue `status`, `height_m`, `repair_suggested`; observation
  `overlay_url`, `recorder`, `event_kind`, `record_hash`.

## Map = trained Gaussian splat

`POST /maps` (multipart):

| field | |
|---|---|
| `splat` | trained, **metric** 3DGS `.ply` (what the viewer loads) |
| `name`, `map_id` | the unit, e.g. "Unit 3.04"; `map_id` is what the app sends |
| `views` | optional zip of the training images + poses (`transforms.json` from nerfstudio, or a COLMAP text model). Needed for the VPS. |
| `metadata` | optional JSON `{"property", "floor_label", "scanned_at", "up_axis": "z", "north": [0, 1], "rooms": [...], "views_to_map": 4x4, "context": {...}}`. `views_to_map` if the view poses are not in the splat's frame (e.g. nerfstudio dataparser normalization). |

In the background the backend extracts the opaque gaussian centers (opacity ≥ 0.2) for lifting,
estimates the floor height, renders a top-down floor plan from the splat colors (dollhouse cut,
2 cm/px) and builds the VPS index from the views (`vps_status`). Rebuild: `POST /maps/{id}/index`.

## Own VPS (free, CPU only)

`VPS=sift` (default), `localization/sift_vps.py`. Does what a hosted VPS such as MultiSet does,
without an API:

1. **Index, once per map:** SIFT keypoints in every training view; the 3D position of each keypoint
   comes from the splat (gaussian centers projected into the view give its depth).
2. **Query:** SIFT on the new image → nearest neighbours in the index → ratio test across distinct 3D
   points → 2D-3D matches → PnP + RANSAC → pose in the splat.
3. **Multi-frame:** 4–6 frames are localized; their ARKit poses give one robust ARKit→map transform,
   which places the close-up photo (a plain wall alone is often not localizable).

On the rendered test room: new viewpoints localized to < 3 cm / < 0.5°, ~1 s per frame on a laptop
CPU, a full iOS capture placed the damage within 3 cm. Real rooms are harder (lighting changes,
blank walls, repeated patterns); `VPS=hloc` (learned features, needs torch) is the upgrade path.

Needs the training views with poses in the splat's frame; it cannot localize in areas no training
view has seen.

## Inputs

### iOS app (docs/PLAN.md section 10)

1. `POST /v1/captures` `{capture_id, map_id, size_bytes, created_unix}` → `{upload_url, expires_at}` (GCS V4 signed URL; locally a backend URL)
2. `PUT upload_url` with the zip (`capture.json`, `frames/*.jpg`, `photo.jpg`, `imu/*`)
3. `POST /v1/captures/{id}/complete` → `202 {"status": "processing"}`
4. `GET /v1/captures/{id}` → `{status: uploaded | processing | done | failed, detail}`

The backend applies section 13 itself: raw sensor-orientation images are rotated upright, intrinsics
and ARKit poses converted (`localization/cameras.py`, tested against the plan's check). It keeps
4–6 frames (tracking normal, ≥ 0.5 s apart, spread over the capture) for localization.

### `POST /inspect` (one image; robots, glasses, tests)

Multipart `image` + `metadata`:

```json
{
  "map_id": "office-floor-3",
  "intrinsics": {"fx": 1400, "fy": 1400, "cx": 960, "cy": 720, "width": 1920, "height": 1440},
  "interface_orientation": "landscape",
  "camera_to_map": [16 numbers or 4x4],        // if the device is already localized
  "camera_to_world": [16 numbers or 4x4],      // device tracking pose (optional)
  "pose_convention": "arkit",                  // or "opencv"
  "source": {"kind": "robot", "device_id": "patrol-02"},
  "timestamp": "2026-11-14T16:42:10Z"
}
```

Synchronous; returns the processed capture with its observations.

### `POST /localize`, `POST /localize/multi`

* `/localize`: `image` + `{map_id, intrinsics, interface_orientation}` → `{camera_pose: {t, q}, camera_to_map, confidence, num_inliers}`
* `/localize/multi`: 4–6 `images` + `{map_id, frames: [{intrinsics, camera_to_world (ARKit), interface_orientation}]}`
  → `{device_to_map, confidence, frames: [...]}` – like MultiSet's multi-image query; an app can
  apply `device_to_map` to its ARKit world to show map content (e.g. known issues) in AR.

503 without a VPS index.

## Outputs for the viewer

* `GET /maps/{id}` → `splat_url` and stats
* `GET /maps/{id}/issues?type=&severity=high,critical&status=&source=robot&as_of=<date>` → issue pins with photo timelines (`as_of` = time slider)
* `GET /issues/{issue_id}` → the record from README step 6:

```json
{
  "issue_id": "ISS-0001", "status": "worsening", "type": "crack", "label": "Crack", "severity": "critical",
  "object": null, "position_m": [0.0, 0.0, 3.0], "normal": [0, 0, -1], "size": 1.8,
  "repair_suggested": false,
  "observations": [{
    "obs_id": "OBS-0002", "timestamp": "...", "source": {"kind": "robot", "device_id": "patrol-02"},
    "camera_pose": {"t": [0, 0, 0], "q": [0, 0, 0, 1]}, "pose_confidence": 1.0,
    "image_url": "...", "mask_url": "...", "overlay_url": "...",
    "length_m": 1.8, "area_m2": 0.43, "detector_confidence": 0.9, "change": "worsening",
    "review": "unreviewed", "reviewed_by": null,
    "points_url": "... [[x,y,z], ...] on the surface", "gaussians_url": "... gaussian indices in the splat"
  }]
}
```

* `GET /maps/{id}/report.csv` → inspection report
* `GET /captures/{id}` → everything about one capture (quality, localization, detections, frames)

Conventions: map coordinates in meters (splat frame); `camera_pose.q` = camera→map rotation as
`[x, y, z, w]` with OpenCV camera axes for the upright image; matrices row-major.

## Human review

* `PATCH /issues/{issue_id}` `{status: "repaired"}`, `{severity: "high"}` (manual), `{object_id, object_label}`
* `PATCH /observations/{obs_id}` `{review: "confirmed" | "rejected", reviewed_by}`; an issue whose
  observations are all rejected is closed as `rejected`. Reviews are the training data for the detector.

## How the steps are implemented

| README step | Implementation |
|---|---|
| 2 Quality gate | sharpness (Laplacian variance) + brightness → flag (`REJECT_LOW_QUALITY_IMAGES` to reject); pose confidence below `MIN_POSE_CONFIDENCE` → `needs_review`, nothing logged |
| 2 Localize | `localization/`: client pose, or VPS on frames + ARKit (`T = C_map · C_device⁻¹`, robust consensus, outlier frames dropped), or VPS on the photo |
| 3 Detect | SAM 3 via Roboflow, one request with all prompts of the enabled classes, per-class thresholds, cross-class dedupe |
| 3 Severity | base by type + size steps (cracks: 0.5 / 1.5 m, areas: 0.1 / 0.5 m²) + structural object label |
| 4 Lift | opaque gaussian centers projected into the photo with a coarse z-buffer; points inside the mask → centroid (median), normal, area (m²), length (main axis, m) |
| 5 Match | same type within 15 cm (and same object if known) → observation; > 20 % growth → `worsening`; open issue visible in a confident view without a finding → `repair_suggested` (human confirms) |
| 6 Store | issues + observations, masks, overlays, 3D points and gaussian indices |

Damage classes (`inspection/classes.py`), labels as in the UX: Crack, Mould, Stain, Scratch, Peeling
paint, Hole, Corrosion are enabled (crack / mould / stain / peeling paint / rust prompts checked on real
photos; scratch and hole barely tested). Dent, exposed wiring and obstruction/spill are disabled;
"missing part" cannot be found with a text prompt.

## External VPS (e.g. MultiSet)

If localization runs elsewhere: take the frames from `GET /captures/{id}` (`frames[].image_url`,
intrinsics, device pose), localize them and post the result:

`POST /captures/{id}/registration` `{"frame_poses": [{"frame": "000012", "camera_to_map": 4x4}, ...]}`
(or `camera_to_map` for the photo, or `device_to_map`). The backend combines the frames, places the
damages and matches issues.

## Deployment (Google Cloud)

Step by step in [deploy/DEPLOY.md](deploy/DEPLOY.md): Cloud Run (`--no-cpu-throttling`, 2 vCPU / 4 GB),
Cloud Storage with signed URLs (app uploads, splat uploads, viewer downloads), Cloud SQL (Postgres),
Secret Manager. VPS indexes are pushed to the bucket (`vps/<map_id>/`) and pulled by new instances,
because Cloud Run disks are ephemeral.

Large splats: Cloud Run accepts at most 32 MB per request, so use
`POST /maps/upload-urls` → `PUT` splat / views to the signed URLs → `POST /maps/register`
(`POST /maps` with a direct upload is for local use).

## Status

Tested (31 tests): viewer adapter (shape, frame conversion, pipeline stages); splat upload via signed URLs, VPS index surviving a container restart; the imoscan flow on a rendered room (unit with rooms → record → move-out handover
linked to the move-in record → live in-view → type correction → repaired → rooms renamed → PDF →
tampered record detected), places (rooms, compass walls, height), floor plan, hash chain; camera conversions against the app plan's check, lifting (area, length, occlusion),
frame-pose alignment with outliers, severity and matching, end-to-end `/inspect` (new → worsening →
repair suggested → repaired), review, `/v1` zip upload, external frame poses, and the own SIFT VPS on
a rendered room (single views, `/localize/multi`, full iOS capture with raw portrait frames).

Not tested yet:
* **Own VPS on real data:** only tested on a synthetic room with clean textures.
* **hloc VPS** (`localization/hloc_vps.py`): written against the hloc API, never run (needs torch,
  hloc and real map views).
* **GCS storage / Cloud SQL / Docker image / deploy commands**: written, not deployed yet (the storage
  sync and signed-URL flows are tested against local storage).
* **Real data**: no real splat or iOS capture has gone through the pipeline yet; the SAM 3 detector
  was checked separately on real photos.
* Lifting uses gaussian centers, not a rendered depth map; large or blurry gaussians can make
  positions noisier than a depth render would.
