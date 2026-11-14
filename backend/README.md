# Damage Detection Backend

FastAPI backend: receives a photo + short video from the app, detects building damage with
SAM 3 (Roboflow serverless), hands the video to the Gaussian-splat team when damage is found,
and projects the 2D damage masks into the returned splat as 3D annotations for the frontend.

```
App ── POST /captures (photo + video + metadata)
          │
          ▼  background
     SAM 3 detection ──► no damage → status "no_damage"
          │ damage
          ▼
     ReconstructionJob "pending" ──► splat team: claim → train → POST result (.ply + photo pose)
                                                                    │
                                                                    ▼ background
                                         mask → 3D projection → damages_3d → status "completed"
Frontend ── GET /captures, GET /captures/{id}
```

## Run

```bash
cp .env.example .env          # set ROBOFLOW_API_KEY (or DETECTOR=fake for offline work)
uv run uvicorn app.main:app --reload
# interactive API docs: http://localhost:8000/docs
uv run pytest                 # tests use the fake detector, no API calls
```

Docker (also runs as-is on Google Cloud Run, which sets `$PORT`):

```bash
docker build -t damage-backend . && docker run -p 8000:8000 --env-file .env damage-backend
```

## Capture status

`received → detecting → no_damage` or
`received → detecting → damage_detected → reconstructing → localizing → completed`;
`failed` at any step (see `error`). Without a video, a damaged capture stays at `damage_detected`.

## For the app

`POST /captures` as `multipart/form-data`:

| field      | required | content |
|------------|----------|---------|
| `image`    | yes      | JPEG / PNG / HEIC photo of the damage (EXIF rotation is applied) |
| `video`    | no       | short video of the surroundings (needed for 3D) |
| `metadata` | no       | JSON string, see below |
| `arkit_frames` | no   | JSON file with per-frame ARKit poses of the video (passed through to the splat team) |

```json
{
  "source_type": "phone",
  "device_model": "iPhone 15 Pro",
  "device_os": "iOS 19.1",
  "app_version": "0.3.0",
  "captured_at": "2026-11-14T15:30:00Z",
  "location": {"latitude": 46.379, "longitude": 7.627, "altitude_m": 1411, "accuracy_m": 5, "heading_deg": 270},
  "context": {"building": "Haus A", "floor": "2", "room": "Bad", "notes": "..."},
  "arkit": {
    "matrix": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
    "fx": 1450.2, "fy": 1450.2, "cx": 960.1, "cy": 719.8,
    "width": 1920, "height": 1440,
    "timestamp": 12345.678, "tracking_state": "normal"
  }
}
```

**ARKit photo pose** (`arkit`, strongly recommended): take everything from the *same* `ARFrame` as the photo.

* `matrix` = `frame.camera.transform` (camera→world) as **4 rows**. simd matrices are column-major,
  so row *i* = `[t.columns.0[i], t.columns.1[i], t.columns.2[i], t.columns.3[i]]`.
* `fx = intrinsics.columns.0.x`, `fy = intrinsics.columns.1.y`, `cx = intrinsics.columns.2.x`,
  `cy = intrinsics.columns.2.y`; `width/height = camera.imageResolution`.
* Send the photo **unrotated** (landscape sensor orientation, e.g. `frame.capturedImage` as JPEG,
  no EXIF rotation). The intrinsics refer to that orientation; a portrait JPEG is rejected with 422.

**`arkit_frames`** suggested format (stored as-is, the splat team defines what they need):
`{"frames": [{"t": <seconds since video start>, "timestamp": <ARFrame.timestamp>, "matrix": [[4 rows]],
"fx": ..., "fy": ..., "cx": ..., "cy": ..., "width": ..., "height": ...}, ...]}`

Response `202` with the capture (status `received`). Poll `GET /captures/{id}` (detection takes ~2–4 s).

## For the frontend

* `GET /captures?has_damage=true&damage_class=mold&status=completed&limit=100` – list for maps / tables
* `GET /captures/{id}` – everything about one capture:
  * `image`, `video` – URLs + size, duration, fps, resolution
  * `location`, `context`, `device_*`, `captured_at`
  * `detection` – summary: `has_damage`, `classes`, `max_confidence`, `model`, `latency_s`, `overlay_url` (photo with masks drawn)
  * `detections[]` – per damage: `damage_class`, `label` (German), `confidence`, `bbox`, `polygons` (pixel coords of `image`), `area_ratio`, `covers_full_frame`
  * `splat` – per-capture reconstruction job: `status`, `splat_url` (.ply), `camera` (pose of the photo in the splat), `scene_info`
  * `scene_id`, `registration` – building scan + alignment ARKit → scan (`ok`, `method`, `fitness`, `rmse`, `ambiguous`, `matrix`, `photo_camera_scene`)
  * `damages_3d[]` – per localized damage, **in splat world coordinates**: `centroid`, `bbox_min`/`bbox_max`, `normal` (points towards the camera), `area_est` (approx. area, m² for ARKit scenes, ignores surface tilt), `n_points`, `indices_url` (JSON list of splat vertex indices inside the damage → highlight those gaussians)
  * `image.camera` – ARKit pose of the photo (e.g. to show the photo frustum in the 3D view)
* `GET /meta/damage-classes` – classes, German labels, prompts, thresholds (legend / filters)
* `GET /scenes/{id}` – building scan: `preview_url` (3 cm .ply for the viewer), bounds, stats
* `GET /scenes/{id}/damages` – all damages localized in one building scan, with photo URLs

Scale: if the splat is trained in the ARKit world frame, all 3D values are in **meters**
(`scene.scene_info.units`). Otherwise units are arbitrary.

## For the splat team

Pull model – your worker needs no inbound connection:

1. `GET /reconstruction-jobs?status=pending` → jobs with `video_url`, `image_url`, `image_width/height`,
   `photo_camera` (ARKit pose of the photo) and `arkit_frames_url` (ARKit poses of the video frames)
2. `POST /reconstruction-jobs/{id}/claim` `{"worker_id": "gpu-1"}` (409 if taken)
3. Train the splat. Two options:
   * **Recommended – ARKit frame:** use the ARKit poses from `arkit_frames_url` as camera poses
     (no COLMAP, metric scale) and keep the splat in the ARKit world frame. Then omit `camera`
     in step 4 – the backend uses `photo_camera`.
   * **Own frame (COLMAP etc.):** include the damage photo (`image_url`) as an extra SfM image
     and send its pose as `camera` in step 4.
4. `POST /reconstruction-jobs/{id}/result` (multipart):
   * `splat`: 3DGS `.ply` (vertex `x, y, z`; `opacity` logit is used to skip transparent floaters)
   * `camera` (optional if trained in the ARKit frame): pose + intrinsics of the damage photo, one of:
     ```json
     {"convention": "colmap",     "qvec": [qw, qx, qy, qz], "tvec": [tx, ty, tz], "fx": 0, "fy": 0, "cx": 0, "cy": 0, "width": 0, "height": 0}
     {"convention": "w2c_opencv", "matrix": [[4x4 world->camera]], "fx": ..., "width": ..., "height": ...}
     {"convention": "c2w_opengl", "matrix": [[4x4 camera->world, nerfstudio transforms.json]], "fx": ..., ...}
     {"convention": "arkit",      "matrix": [[4x4 ARKit camera.transform as rows]], "fx": ..., ...}
     ```
     `width/height` are those of the image the pose belongs to (may differ from the upload; masks are rescaled).
     The pose and the .ply **must be in the same coordinate frame** (watch out for nerfstudio's
     `dataparser_transforms.json` normalization when exporting).
   * `scene_info` (optional JSON): `coordinate_frame` ("arkit" / "colmap"), `units` ("m"), software, training time, …
5. On error: `POST /reconstruction-jobs/{id}/fail` `{"error": "..."}`

Optional push: set `SPLAT_WEBHOOK_URL` and the job JSON is POSTed there when a job is created.

## Building scan (pre-scanned map)

The LiDAR-scanner point cloud is the reference map. Damages are placed directly in it (meters),
no per-capture splat needed.

1. `POST /scenes` (multipart: `cloud` .ply, `name`, `metadata` `{"up_axis": "z"}`) → downsampled in the background (`processing` → `ready`)
2. Captures send `scene_id` (+ optional `scene_hint` `{"center": [x,y,z], "radius": 2.5}`, `arkit_session_id`) in `metadata`
3. Alignment ARKit world → scan, in this order:
   * manual (`POST /captures/{id}/registration` with `matrix` or ≥ 3 `pairs` of `{arkit, scene}` points)
   * reuse from an earlier capture of the same `arkit_session_id`
   * automatic from an ARKit LiDAR point cloud (`geometry` upload): FPFH global registration +
     yaw/position sweep around `scene_hint`, ICP refinement, ambiguity check
4. Masks are projected into the scan → `damages_3d` with `frame: "scene"`

If the alignment is uncertain the capture goes to `needs_alignment` instead of producing a wrong
position. On synthetic data: without a hint, feature matching finds the pose in about half the
cases (and reports failure otherwise); with a hint ~1 m off, all test cases align to < 3 cm / < 1°
in ~10 s on a laptop CPU.

## Code map

| file | purpose |
|------|---------|
| `app/main.py` | routes |
| `app/schemas.py` | API contract (request / response models) |
| `app/models.py` | DB tables (SQLite now, any SQLAlchemy URL later) |
| `app/pipeline.py` | background steps: detection, localization |
| `app/detection/` | `Detector` interface, Roboflow SAM 3, fake detector, class/prompt config |
| `app/localization.py` | PLY reader, camera conventions, mask → 3D projection |
| `app/registration.py` | ARKit capture → building scan alignment (Open3D), scene preprocessing |
| `tools/` | SAM 3 evaluation scripts (Wikimedia test set, prompt / threshold tuning) |
| `app/storage.py` | blob storage interface (local now; S3-compatible for R2 / GCS later) |
| `app/media.py` | EXIF normalization, ffprobe, overlay rendering |

## Status vs. project plan (README on `main`, iOS plan on `mobile-recorder-app`)

Not aligned yet:

* **Upload contract:** the iOS app uploads one zip (`capture.json`, 5 FPS frames, `photo.jpg`, IMU)
  via a GCS signed URL (`POST /v1/captures` → `PUT` → `POST /v1/captures/{id}/complete`). This
  backend currently takes a multipart upload of photo + video. The `/v1` flow, zip parsing and the
  portrait rotation from the app plan (section 13) still need to be built.
* **Localization:** the project plans visual positioning (MultiSet or hloc) of video frames in the
  map, carried over to the photo through the ARKit poses. Here, the ARKit → map alignment exists
  (manual, per session, or from LiDAR geometry), but no VPS. A VPS result plugs in as the session
  alignment (`T = pose_map · pose_arkit⁻¹`).
* **Issue tracking:** issues with observations, matching (same type within 15 cm), status
  new / worsening / stable / repaired and severity are not built yet; `damages_3d` are per capture.
* **Damage classes:** tuned for apartments (crack, mold, water damage, rust, peeling paint, ...);
  the project list (corrosion, leak/stain, dent, exposed wiring, coating damage, ...) still needs
  prompts and tests.
* **Layout:** detection, lifting and registration live in `backend/app/` (one deployable service)
  rather than separate `inspection/` and `localization/` folders.

## Deployment (Google Cloud)

* Cloud Run from the Dockerfile. Background tasks run after the response, so use instance-based
  billing ("CPU always allocated") or move them to Cloud Tasks.
* The container filesystem is ephemeral: use Cloud SQL (Postgres) via `DATABASE_URL` and a GCS
  implementation of `Storage` (signed URLs) instead of SQLite / local files.
* Open3D makes the image large (~1.3 GB venv); registration wants ≥ 2 vCPU and 4 GB RAM.

## Next steps / not done yet

* **Drone**: `POST /detect` is the single-frame path (synchronous, nothing stored). Roboflow takes
  ~2 s per frame, so real-time needs a self-hosted detector behind the same `Detector` interface,
  plus a stream ingest (WebRTC/RTSP → frame sampler). Drone GPS/gimbal poses plug in like ARKit
  poses (`photo_camera` with convention `w2c_opencv` / `c2w_opengl`).
* **LiDAR depth**: with ARKit `sceneDepth` uploaded alongside the photo, damages could be placed in
  3D immediately, before (or without) a splat.
* **Deployment**: swap `LocalStorage` for an S3-compatible implementation with signed URLs,
  SQLite for Postgres, background tasks for a real queue (Cloud Tasks / Cloudflare Queues).
* **Auth**: none yet — add API keys for app / splat worker before exposing it publicly.
* **False positives**: thresholds were tuned on damaged images only; test on clean walls too.
