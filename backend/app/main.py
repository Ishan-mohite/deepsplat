import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from .db import get_session, init_db
from .detection import DAMAGE_CLASSES, get_detector
from .localization import Camera, check_aspect
from .media import normalize_image, probe_video
from .localization import read_ply_points
from .models import (Capture, CaptureStatus, Damage3D, Detection, JobStatus, ReconstructionJob, Scene,
                     SceneStatus, now)
from .pipeline import run_detection, run_localization, run_scene_localization, run_scene_preprocessing
from .schemas import (CaptureListItem, CaptureMetadata, CaptureOut, Damage3DOut, DetectFrameOut, JobOut,
                      SceneOut, capture_list_item, capture_out, damage3d_out, detection_out, job_out,
                      scene_out)
from .storage import storage

try:  # iPhone photos are often HEIC
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Damage Detection Backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
SessionDep = Annotated[Session, Depends(get_session)]


def _parse_json(raw: str | None, field: str) -> dict:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"`{field}` is not valid JSON: {e}")
    if not isinstance(val, dict):
        raise HTTPException(422, f"`{field}` must be a JSON object")
    return val


def _read_image(upload: UploadFile) -> tuple[bytes, int, int, str]:
    try:
        return normalize_image(upload.file.read())
    except Exception:
        raise HTTPException(415, "image could not be decoded (use JPEG, PNG or HEIC)")


# ---------------------------------------------------------------- meta

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/meta/damage-classes")
def damage_classes():
    """Classes, labels, prompts and thresholds (for frontend legends / filters)."""
    return DAMAGE_CLASSES


# ---------------------------------------------------------------- captures (app -> backend)

@app.post("/captures", response_model=CaptureOut, status_code=202)
def create_capture(
    s: SessionDep,
    bg: BackgroundTasks,
    image: Annotated[UploadFile, File(description="photo of the (possible) damage")],
    video: Annotated[UploadFile | None, File(description="short video of the surroundings for 3D reconstruction")] = None,
    metadata: Annotated[str | None, Form(description="CaptureMetadata as JSON string")] = None,
    arkit_frames: Annotated[UploadFile | None, File(description="optional JSON with per-video-frame ARKit poses")] = None,
    geometry: Annotated[UploadFile | None, File(
        description="optional .ply: ARKit LiDAR points around the photo, in ARKit world coordinates "
                    "(used to align the capture with the building scan)")] = None,
):
    """Upload photo + video. Detection runs in the background; poll GET /captures/{id}."""
    try:
        meta = CaptureMetadata.model_validate(_parse_json(metadata, "metadata"))
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))
    img_bytes, w, h, sha = _read_image(image)

    photo_camera = {}
    if meta.arkit:
        photo_camera = meta.arkit.as_camera_dict()
        try:
            check_aspect(Camera.from_dict(photo_camera), (w, h))
        except Exception as e:
            raise HTTPException(422, f"invalid arkit camera: {e}")
    if meta.scene_id and s.get(Scene, meta.scene_id) is None:
        raise HTTPException(404, f"scene {meta.scene_id} not found")
    frames_bytes = None
    if arkit_frames is not None and arkit_frames.filename:
        frames_bytes = arkit_frames.file.read()
        try:
            json.loads(frames_bytes)
        except json.JSONDecodeError as e:
            raise HTTPException(422, f"`arkit_frames` is not valid JSON: {e}")

    cap = Capture(image_key="", source_type=meta.source_type, device_model=meta.device_model,
                  device_os=meta.device_os, app_version=meta.app_version, captured_at=meta.captured_at,
                  context=meta.context, image_content_type=image.content_type,
                  image_width=w, image_height=h, image_sha256=sha, photo_camera=photo_camera,
                  arkit_session_id=meta.arkit_session_id, scene_id=meta.scene_id,
                  scene_hint=meta.scene_hint.model_dump() if meta.scene_hint else {})
    if meta.location:
        loc = meta.location
        cap.latitude, cap.longitude, cap.altitude_m = loc.latitude, loc.longitude, loc.altitude_m
        cap.gps_accuracy_m, cap.heading_deg = loc.accuracy_m, loc.heading_deg

    cap.image_key = f"captures/{cap.id}/image.jpg"
    storage.put_bytes(cap.image_key, img_bytes)
    if geometry is not None and geometry.filename:
        cap.geometry_key = f"captures/{cap.id}/geometry.ply"
        storage.put_file(cap.geometry_key, geometry.file)
        try:
            read_ply_points(storage.local_path(cap.geometry_key))
        except Exception as e:
            raise HTTPException(422, f"`geometry` is not a readable .ply point cloud: {e}")
    if frames_bytes is not None:
        cap.arkit_frames_key = f"captures/{cap.id}/arkit_frames.json"
        storage.put_bytes(cap.arkit_frames_key, frames_bytes)
    if video is not None and video.filename:
        ext = Path(video.filename).suffix.lower() or ".mp4"
        cap.video_key = f"captures/{cap.id}/video{ext}"
        cap.video_content_type = video.content_type
        cap.video_size_bytes = storage.put_file(cap.video_key, video.file)
        for k, v in probe_video(storage.local_path(cap.video_key)).items():
            setattr(cap, k, v)

    s.add(cap)
    s.commit()
    s.refresh(cap)
    bg.add_task(run_detection, cap.id)
    return capture_out(s, cap)


@app.get("/captures", response_model=list[CaptureListItem])
def list_captures(
    s: SessionDep,
    has_damage: bool | None = None,
    status: CaptureStatus | None = None,
    damage_class: str | None = None,
    limit: Annotated[int, Query(le=500)] = 100,
    offset: int = 0,
):
    q = select(Capture).order_by(Capture.created_at.desc())
    if has_damage is not None:
        q = q.where(Capture.has_damage == has_damage)
    if status is not None:
        q = q.where(Capture.status == status)
    items = [capture_list_item(s, c) for c in s.exec(q.offset(offset).limit(limit)).all()]
    if damage_class:
        items = [i for i in items if damage_class in i.classes]
    return items


@app.get("/captures/{capture_id}", response_model=CaptureOut)
def get_capture(capture_id: str, s: SessionDep):
    cap = s.get(Capture, capture_id)
    if cap is None:
        raise HTTPException(404, "capture not found")
    return capture_out(s, cap)


@app.post("/captures/{capture_id}/redetect", response_model=CaptureOut, status_code=202)
def redetect(capture_id: str, s: SessionDep, bg: BackgroundTasks):
    """Re-run detection (e.g. after changing prompts or thresholds)."""
    cap = s.get(Capture, capture_id)
    if cap is None:
        raise HTTPException(404, "capture not found")
    bg.add_task(run_detection, cap.id)
    return capture_out(s, cap)


class RegistrationIn(BaseModel):
    """Manual alignment: either a 4x4 matrix (ARKit world -> scene, rows) or >= 3 point pairs."""
    matrix: list[list[float]] | None = None
    pairs: list[dict] | None = None   # [{"arkit": [x,y,z], "scene": [x,y,z]}, ...]


@app.post("/captures/{capture_id}/registration", response_model=CaptureOut, status_code=202)
def set_registration(capture_id: str, body: RegistrationIn, s: SessionDep, bg: BackgroundTasks):
    """Align a capture with its building scan by hand (fallback when status is needs_alignment)."""
    cap = s.get(Capture, capture_id)
    if cap is None:
        raise HTTPException(404, "capture not found")
    if not cap.scene_id:
        raise HTTPException(409, "capture has no scene_id")
    if body.matrix is not None:
        if len(body.matrix) != 4 or any(len(r) != 4 for r in body.matrix):
            raise HTTPException(422, "matrix must be 4x4")
        manual = {"matrix": body.matrix}
    elif body.pairs and len(body.pairs) >= 3 and all({"arkit", "scene"} <= p.keys() for p in body.pairs):
        manual = {"pairs": body.pairs}
    else:
        raise HTTPException(422, "give `matrix` or >= 3 `pairs` of {arkit, scene} points")
    bg.add_task(run_scene_localization, cap.id, manual)
    return capture_out(s, cap)


# ---------------------------------------------------------------- building scans

@app.post("/scenes", response_model=SceneOut, status_code=202)
def create_scene(
    s: SessionDep,
    bg: BackgroundTasks,
    cloud: Annotated[UploadFile, File(description="building scan point cloud (.ply, meters)")],
    name: Annotated[str, Form()],
    metadata: Annotated[str | None, Form(description='JSON: {"up_axis": "z", "units": "m", "context": {...}}')] = None,
):
    """Upload a pre-scanned building. Downsampling runs in the background (status processing -> ready)."""
    meta = _parse_json(metadata, "metadata")
    up_axis = meta.get("up_axis", "z")
    if up_axis not in ("z", "y"):
        raise HTTPException(422, "up_axis must be 'z' or 'y'")
    if Path(cloud.filename or "").suffix.lower() != ".ply":
        raise HTTPException(415, "scene cloud must be a .ply")
    sc = Scene(name=name, up_axis=up_axis, units=meta.get("units", "m"), context=meta.get("context", {}),
               cloud_key="")
    sc.cloud_key = f"scenes/{sc.id}/cloud.ply"
    storage.put_file(sc.cloud_key, cloud.file)
    s.add(sc)
    s.commit()
    s.refresh(sc)
    bg.add_task(run_scene_preprocessing, sc.id)
    return scene_out(sc)


@app.get("/scenes", response_model=list[SceneOut])
def list_scenes(s: SessionDep):
    return [scene_out(sc) for sc in s.exec(select(Scene).order_by(Scene.created_at.desc())).all()]


@app.get("/scenes/{scene_id}", response_model=SceneOut)
def get_scene(scene_id: str, s: SessionDep):
    sc = s.get(Scene, scene_id)
    if sc is None:
        raise HTTPException(404, "scene not found")
    return scene_out(sc)


class SceneDamageOut(Damage3DOut):
    capture_status: str
    image_url: str | None
    overlay_url: str | None
    captured_at: str | None


@app.get("/scenes/{scene_id}/damages", response_model=list[SceneDamageOut])
def scene_damages(scene_id: str, s: SessionDep, damage_class: str | None = None):
    """All localized damages in one building scan (for the 3D building view)."""
    if s.get(Scene, scene_id) is None:
        raise HTTPException(404, "scene not found")
    q = select(Damage3D).where(Damage3D.scene_id == scene_id, Damage3D.frame == "scene")
    if damage_class:
        q = q.where(Damage3D.damage_class == damage_class)
    out = []
    for d in s.exec(q).all():
        c = s.get(Capture, d.capture_id)
        out.append(SceneDamageOut(
            **damage3d_out(d).model_dump(), capture_status=c.status,
            image_url=storage.url(c.image_key), overlay_url=storage.url(c.overlay_key) if c.overlay_key else None,
            captured_at=(c.captured_at or c.created_at).isoformat()))
    return out


# ---------------------------------------------------------------- single-frame detection (drone / live)

@app.post("/detect", response_model=DetectFrameOut)
def detect_frame(image: Annotated[UploadFile, File()]):
    """Synchronous detection on one frame, nothing stored. Same code path a drone stream will use."""
    img_bytes, w, h, _ = _read_image(image)
    try:
        r = get_detector().detect(img_bytes, w, h)
    except Exception as e:
        raise HTTPException(502, f"detector error: {e}")
    return DetectFrameOut(has_damage=bool(r.detections), model=r.model, latency_s=r.latency_s,
                          width=w, height=h, detections=[detection_out(d) for d in r.detections])


# ---------------------------------------------------------------- reconstruction jobs (splat team)

class ClaimIn(BaseModel):
    worker_id: str


class FailIn(BaseModel):
    error: str


def _job(s: Session, job_id: str) -> ReconstructionJob:
    job = s.get(ReconstructionJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.get("/reconstruction-jobs", response_model=list[JobOut])
def list_jobs(s: SessionDep, status: JobStatus | None = JobStatus.pending, limit: int = 20):
    q = select(ReconstructionJob).order_by(ReconstructionJob.created_at)
    if status is not None:
        q = q.where(ReconstructionJob.status == status)
    return [job_out(s, j) for j in s.exec(q.limit(limit)).all()]


@app.get("/reconstruction-jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, s: SessionDep):
    return job_out(s, _job(s, job_id))


@app.post("/reconstruction-jobs/{job_id}/claim", response_model=JobOut)
def claim_job(job_id: str, body: ClaimIn, s: SessionDep):
    job = _job(s, job_id)
    if job.status != JobStatus.pending:
        raise HTTPException(409, f"job is {job.status}")
    job.status, job.claimed_by, job.claimed_at = JobStatus.claimed, body.worker_id, now()
    cap = s.get(Capture, job.capture_id)
    cap.status, cap.updated_at = CaptureStatus.reconstructing, now()
    s.add_all([job, cap])
    s.commit()
    return job_out(s, job)


@app.post("/reconstruction-jobs/{job_id}/result", response_model=JobOut, status_code=202)
def submit_result(
    job_id: str,
    s: SessionDep,
    bg: BackgroundTasks,
    splat: Annotated[UploadFile, File(description="3DGS .ply (vertex x,y,z[,opacity,...])")],
    camera: Annotated[str | None, Form(
        description="pose + intrinsics of the damage photo in the splat frame, JSON (see README). "
                    "Omit if the splat was trained in the ARKit world frame: the app's photo pose is used.")] = None,
    scene_info: Annotated[str | None, Form(description="optional JSON: coordinate_frame, units, software, ...")] = None,
):
    """Deliver the splat. Damage masks are then projected into it in the background."""
    job = _job(s, job_id)
    if job.status not in (JobStatus.pending, JobStatus.claimed):
        raise HTTPException(409, f"job is {job.status}")
    cap = s.get(Capture, job.capture_id)
    cam = _parse_json(camera, "camera") or cap.photo_camera
    if not cam:
        raise HTTPException(422, "no `camera` given and the capture has no ARKit photo pose")
    try:
        check_aspect(Camera.from_dict(cam), (cap.image_width, cap.image_height))
    except Exception as e:
        raise HTTPException(422, f"invalid camera: {e}")
    ext = Path(splat.filename or "splat.ply").suffix.lower()
    if ext != ".ply":
        raise HTTPException(415, "splat must be a .ply (other formats can be added as extra assets later)")

    job.splat_key = f"captures/{job.capture_id}/splat_{job.id}.ply"
    storage.put_file(job.splat_key, splat.file)
    job.splat_format, job.camera = "3dgs-ply", cam
    job.scene_info = _parse_json(scene_info, "scene_info")
    job.status, job.completed_at = JobStatus.completed, now()
    s.add(job)
    s.commit()
    bg.add_task(run_localization, job.id)
    return job_out(s, job)


@app.post("/reconstruction-jobs/{job_id}/fail", response_model=JobOut)
def fail_job(job_id: str, body: FailIn, s: SessionDep):
    job = _job(s, job_id)
    job.status, job.error, job.completed_at = JobStatus.failed, body.error, now()
    cap = s.get(Capture, job.capture_id)
    cap.status, cap.error, cap.updated_at = CaptureStatus.failed, f"reconstruction: {body.error}", now()
    s.add_all([job, cap])
    s.commit()
    return job_out(s, job)


# ---------------------------------------------------------------- files (local storage only)

@app.get("/files/{key:path}")
def get_file(key: str):
    try:
        path = storage.local_path(key)
    except ValueError:
        raise HTTPException(400, "invalid key")
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path)
