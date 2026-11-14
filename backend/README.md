# DeepSplat backend

Implements the "Inspect & log damage" pipeline from the project README, for the iOS capture app
(`docs/PLAN.md` on `mobile-recorder-app`), robots / glasses and the digital-twin viewer.

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
| `localization/` | camera conventions of the app, frame selection, frame-pose alignment, VPS interface, hloc VPS |
| `tools/` | SAM 3 evaluation scripts (test set from Wikimedia Commons, prompt / threshold tuning) |
| `tests/` | unit + end-to-end tests (fake detector, fake VPS, synthetic splat), ~2 s |

## Run locally

```bash
uv sync
cp backend/.env.example .env           # set ROBOFLOW_API_KEY, or DETECTOR=fake
uv run uvicorn backend.main:app --reload
# API docs: http://localhost:8000/docs
uv run pytest
```

## Map = trained Gaussian splat

`POST /maps` (multipart):

| field | |
|---|---|
| `splat` | trained, **metric** 3DGS `.ply` (what the viewer loads) |
| `name`, `map_id` | `map_id` is what the app sends, e.g. `office-floor-3` |
| `views` | optional zip of the training images + poses (`transforms.json` from nerfstudio, or a COLMAP text model). Needed for the VPS. |
| `metadata` | optional JSON `{"up_axis": "z", "views_to_map": 4x4, "context": {...}}`. `views_to_map` if the view poses are not in the splat's frame (e.g. nerfstudio dataparser normalization). |

In the background the backend extracts the opaque gaussian centers (opacity ≥ 0.2) for lifting
and, with `VPS=hloc`, builds the VPS index from the views (`vps_status`). Rebuild: `POST /maps/{id}/index`.

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

### `POST /localize`

`image` + `{map_id, intrinsics, interface_orientation}` → `{camera_pose: {t, q}, camera_to_map, confidence, num_inliers}`. 503 without a VPS index.

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

Damage classes (`inspection/classes.py`): crack, corrosion, leak/stain and coating damage are enabled
(prompts checked on real photos). Dent, exposed wiring and obstruction/spill have untested prompts and
are disabled; "missing part" cannot be found with a text prompt.

## External VPS (e.g. MultiSet)

If localization runs elsewhere: take the frames from `GET /captures/{id}` (`frames[].image_url`,
intrinsics, device pose), localize them and post the result:

`POST /captures/{id}/registration` `{"frame_poses": [{"frame": "000012", "camera_to_map": 4x4}, ...]}`
(or `camera_to_map` for the photo, or `device_to_map`). The backend combines the frames, places the
damages and matches issues.

## Deployment (Google Cloud)

* Cloud Run from `backend/Dockerfile` (build context = repo root). Background processing of `/v1`
  uploads runs after the response, so use instance-based billing ("CPU always allocated") or Cloud Tasks.
* `STORAGE=gcs` + `GCS_BUCKET`: uploads go straight to GCS via V4 signed URLs. The Cloud Run service
  account needs `roles/iam.serviceAccountTokenCreator` on itself to sign.
* `DATABASE_URL` → Cloud SQL (Postgres). The container filesystem is ephemeral, so no SQLite there.
* `API_TOKEN` → static bearer token for the app (stored in the app's Keychain).
* VPS: `--build-arg WITH_VPS=1` and `VPS=hloc`; index building wants a GPU (Cloud Run with L4).

## Status

Tested (14 tests): camera conversions against the app plan's check, lifting (area, length, occlusion),
frame-pose alignment with outliers, severity and matching, end-to-end `/inspect` (new → worsening →
repair suggested → repaired), review, `/v1` zip upload with a fake VPS, external frame poses.

Not tested yet:
* **hloc VPS** (`localization/hloc_vps.py`): written against the hloc API, never run (needs torch,
  hloc and real map views).
* **GCS storage / Cloud SQL / Docker image**: written, not deployed.
* **Real data**: no real splat or iOS capture has gone through the pipeline yet; the SAM 3 detector
  was checked separately on real photos.
* Lifting uses gaussian centers, not a rendered depth map; large or blurry gaussians can make
  positions noisier than a depth render would.
