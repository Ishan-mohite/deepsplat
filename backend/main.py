"""DeepSplat backend (FastAPI). Endpoints follow the project README (POST /localize, POST /inspect,
issue log for the viewer) and the iOS app plan (/v1 capture upload)."""

import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import numpy as np
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from inspection import evidence
from inspection.classes import DAMAGE_CLASSES, label
from inspection.lifting import Camera, visible_points, is_visible
from inspection.placement import place
from inspection.detection import get_detector
from inspection.severity import LEVELS
from localization import vps as vps_mod
from localization.alignment import alignment_from_frame_poses
from localization.cameras import R_P, Frame, Intrinsics, c2w_to_tq, matrix_from_row_major, to_cv, upright

from . import pipeline, report
from .config import settings
from .db import get_session, init_db
from .models import OPEN_STATUSES, Capture, CaptureStatus, Detection, Handover, Issue, IssueStatus, Map, Observation, aware, now
from .schemas import (DashboardOut, HandoverIn, HandoverOut, InViewIn, InViewOut, InViewRecord, LatestRecord,
                      MapPatch, OpenFinding, floor_plan_px, handover_out, live_observations, place_of)
from .schemas import (CaptureOut, DetectionOut, InspectMetadata, IssueOut, IssuePatch, LocalizeMetadata,
                      LocalizeMultiMetadata, LocalizeMultiOut, LocalizeOut, FrameResult, MapOut, ObservationOut, ObservationReview, RegistrationIn, V1CaptureCreate,
                      V1CaptureCreated, V1CaptureStatus, capture_out, issue_out, map_out, observation_out)
from .storage import LocalStorage, StorageIndexSync, storage

try:  # iPhone photos may be HEIC
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # on GCS the VPS indexes are persisted in the bucket (Cloud Run disks are ephemeral)
    sync = StorageIndexSync(storage) if settings.storage == "gcs" else None
    vps_mod.configure(settings.vps, settings.data_dir / "vps", sync)
    yield


app = FastAPI(title="DeepSplat Backend", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
SessionDep = Annotated[Session, Depends(get_session)]


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    if settings.api_token and authorization != f"Bearer {settings.api_token}":
        raise HTTPException(401, "missing or invalid bearer token")


Auth = Depends(require_token)


# ---------------------------------------------------------------- helpers

def _json_form(raw: str | None, field: str) -> dict:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"`{field}` is not valid JSON: {e}")
    if not isinstance(val, dict):
        raise HTTPException(422, f"`{field}` must be a JSON object")
    return val


def _validated(model: type[BaseModel], raw: str | None, field: str):
    try:
        return model.model_validate(_json_form(raw, field))
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))


def _jpeg(upload: UploadFile, hashes: list | None = None) -> bytes:
    """Re-encode as JPEG without applying EXIF rotation (intrinsics refer to the stored pixels).
    The SHA-256 of the bytes exactly as uploaded is appended to `hashes` (evidence)."""
    raw = upload.file.read()
    if hashes is not None:
        hashes.append((evidence.sha256(raw), len(raw)))
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(415, "image could not be decoded (JPEG, PNG or HEIC)")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _pose(values, convention: str) -> np.ndarray:
    try:
        M = matrix_from_row_major(values)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if convention == "arkit":
        return to_cv(M)
    if convention == "opencv":
        return M
    raise HTTPException(422, "pose_convention must be 'arkit' or 'opencv'")


def _frame(upload: UploadFile, K: Intrinsics, orientation: str, device_c2w=None, hashes: list | None = None) -> Frame:
    img = _jpeg(upload, hashes)
    w, h = Image.open(io.BytesIO(img)).size
    if (w, h) != (K.width, K.height):
        raise HTTPException(422, f"intrinsics are for {K.width}x{K.height} but the image is {w}x{h}")
    try:
        return upright(Frame(name="photo", image=img, K=K, c2w=device_c2w), orientation)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _map(s: Session, map_id: str) -> Map:
    m = s.get(Map, map_id)
    if m is None:
        raise HTTPException(404, f"map {map_id} not found")
    return m


def _capture(s: Session, capture_id: str) -> Capture:
    c = s.get(Capture, capture_id)
    if c is None:
        raise HTTPException(404, "capture not found")
    return c


def _issue(s: Session, issue_id: str) -> Issue:
    try:
        seq = int(issue_id.upper().removeprefix("ISS-"))
    except ValueError:
        raise HTTPException(404, "issue not found")
    i = s.get(Issue, seq)
    if i is None:
        raise HTTPException(404, "issue not found")
    return i


# ---------------------------------------------------------------- meta

@app.get("/health")
def health():
    return {"ok": True, "vps": settings.vps, "detector": settings.detector}


@app.get("/meta/damage-classes")
def damage_classes():
    """Damage types, labels, prompts, thresholds, whether enabled (legend / filters)."""
    return DAMAGE_CLASSES


# ---------------------------------------------------------------- maps

@app.post("/maps", response_model=MapOut, status_code=202, dependencies=[Auth])
def create_map(
    s: SessionDep,
    bg: BackgroundTasks,
    splat: Annotated[UploadFile, File(description="trained, metric Gaussian splat (3DGS .ply)")],
    name: Annotated[str, Form()],
    map_id: Annotated[str | None, Form(description="id the app uses, e.g. office-floor-3")] = None,
    views: Annotated[UploadFile | None, File(description="zip: training images + transforms.json or COLMAP text model")] = None,
    metadata: Annotated[str | None, Form(description='JSON: {"property", "floor_label", "scanned_at", "up_axis": "z", '
                                                      '"north": [0, 1], "rooms": [...], "views_to_map": 4x4, "context": {}}')] = None,
):
    """Register a unit's map with a direct upload (local / small files). On Cloud Run (32 MB request
    limit) use POST /maps/upload-urls + POST /maps/register instead."""
    meta = _json_form(metadata, "metadata")
    if Path(splat.filename or "").suffix.lower() != ".ply":
        raise HTTPException(415, "splat must be a 3DGS .ply")
    mid = map_id or name.lower().replace(" ", "-")
    if s.get(Map, mid) is not None:
        raise HTTPException(409, f"map {mid} already exists")
    storage.put_file(_map_key(mid, "splat"), splat.file, "application/octet-stream")
    has_views = views is not None and bool(views.filename)
    if has_views:
        storage.put_file(_map_key(mid, "views"), views.file, "application/zip")
    return _register_map(s, bg, mid, name, meta, has_views)


MAP_FILES = {"splat": ("splat.ply", "application/octet-stream"), "views": ("views.zip", "application/zip")}


def _map_key(map_id: str, kind: str) -> str:
    return f"maps/{map_id}/{MAP_FILES[kind][0]}"


def _register_map(s: Session, bg: BackgroundTasks, mid: str, name: str, meta: dict, has_views: bool) -> MapOut:
    try:
        unit = MapPatch.model_validate({k: meta[k] for k in MapPatch.model_fields if k in meta})
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json()))
    if meta.get("up_axis", "z") not in ("z", "y"):
        raise HTTPException(422, "up_axis must be 'z' or 'y'")
    m = Map(id=mid, name=name, up_axis=meta.get("up_axis", "z"), context=meta.get("context", {}),
            views_to_map=meta.get("views_to_map"), splat_key=_map_key(mid, "splat"),
            views_key=_map_key(mid, "views") if has_views else None,
            property=unit.property, floor_label=unit.floor_label, scanned_at=unit.scanned_at,
            north=unit.north or [0.0, 1.0], floor_z=unit.floor_z,
            rooms=[r.model_dump() for r in unit.rooms or []])
    s.add(m)
    s.commit()
    s.refresh(m)
    bg.add_task(pipeline.process_map, m.id)
    return map_out(m)


class MapUploadIn(BaseModel):
    map_id: str


class MapRegisterIn(BaseModel):
    map_id: str
    name: str
    metadata: dict = {}


@app.post("/maps/upload-urls", dependencies=[Auth])
def map_upload_urls(body: MapUploadIn, s: SessionDep):
    """Step 1 for large splats: signed PUT URLs for the splat (.ply) and the training views (.zip).
    PUT each file with exactly the given content_type, then POST /maps/register."""
    if s.get(Map, body.map_id) is not None:
        raise HTTPException(409, f"map {body.map_id} already exists")
    out = {}
    for kind, (_, ctype) in MAP_FILES.items():
        url, expires = storage.upload_url(_map_key(body.map_id, kind), ctype)
        out[kind] = {"upload_url": url, "content_type": ctype, "expires_at": expires}
    return out


@app.post("/maps/register", response_model=MapOut, status_code=202, dependencies=[Auth])
def map_register(body: MapRegisterIn, s: SessionDep, bg: BackgroundTasks):
    """Step 2: register the map once the uploads are done (views are optional)."""
    if s.get(Map, body.map_id) is not None:
        raise HTTPException(409, f"map {body.map_id} already exists")
    if not storage.exists(_map_key(body.map_id, "splat")):
        raise HTTPException(409, "splat not uploaded yet")
    return _register_map(s, bg, body.map_id, body.name, body.metadata,
                         storage.exists(_map_key(body.map_id, "views")))


@app.get("/maps", response_model=list[MapOut])
def list_maps(s: SessionDep):
    return [map_out(m) for m in s.exec(select(Map).order_by(Map.created_at.desc())).all()]


@app.get("/maps/{map_id}", response_model=MapOut)
def get_map(map_id: str, s: SessionDep):
    return map_out(_map(s, map_id))


@app.post("/maps/{map_id}/index", response_model=MapOut, status_code=202, dependencies=[Auth])
def rebuild_index(map_id: str, s: SessionDep, bg: BackgroundTasks):
    """(Re)build the VPS index from the uploaded training views."""
    m = _map(s, map_id)
    if not m.views_key:
        raise HTTPException(409, "map has no training views")
    bg.add_task(pipeline.build_vps_index, m.id)
    return map_out(m)


def _issues_query(s: Session, map_id: str | None, type_: str | None, severity: str | None, status: str | None,
                  include_closed: bool, as_of: datetime | None):
    q = select(Issue)
    if map_id:
        q = q.where(Issue.map_id == map_id)
    if type_:
        q = q.where(Issue.damage_class == type_)
    if severity:
        q = q.where(Issue.severity.in_(severity.split(",")))
    if status:
        q = q.where(Issue.status.in_(status.split(",")))
    elif not include_closed:
        q = q.where(Issue.status.in_(OPEN_STATUSES))
    if as_of:
        q = q.where(Issue.first_seen <= as_of)
    return s.exec(q.order_by(Issue.seq)).all()


def _as_of(out: IssueOut, as_of: datetime | None) -> IssueOut | None:
    """Time slider: the issue as it was known at `as_of`."""
    if as_of is None:
        return out
    a = as_of.replace(tzinfo=None)
    obs = [o for o in out.observations if o.timestamp.replace(tzinfo=None) <= a]
    if not obs:
        return None
    out.observations = obs
    out.status = {"new": "new", "worsening": "worsening"}.get(obs[-1].change, "stable")
    out.last_seen = obs[-1].timestamp
    return out


@app.get("/maps/{map_id}/issues", response_model=list[IssueOut])
def map_issues(
    map_id: str,
    s: SessionDep,
    type: str | None = None,
    severity: Annotated[str | None, Query(description="comma-separated")] = None,
    status: Annotated[str | None, Query(description="comma-separated; default: open issues")] = None,
    source: Annotated[str | None, Query(description="only issues observed by this source kind")] = None,
    include_closed: bool = False,
    as_of: Annotated[datetime | None, Query(description="time slider: state at this date")] = None,
):
    """Issue pins for the digital-twin viewer, with photo timelines."""
    _map(s, map_id)
    out = []
    for i in _issues_query(s, map_id, type, severity, status, include_closed or as_of is not None, as_of):
        rec = _as_of(issue_out(s, i), as_of)
        if rec is None or (source and not any(o.source.kind == source for o in rec.observations)):
            continue
        out.append(rec)
    return out


@app.get("/maps/{map_id}/report.csv")
def map_report(map_id: str, s: SessionDep, include_closed: bool = True):
    """Inspection report as CSV (one row per issue)."""
    _map(s, map_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["issue_id", "type", "severity", "status", "x_m", "y_m", "z_m", "size", "first_seen",
                "last_seen", "observations", "repair_suggested", "object", "last_image_url"])
    for i in _issues_query(s, map_id, None, None, None, include_closed, None):
        rec = issue_out(s, i)
        last = rec.observations[-1] if rec.observations else None
        w.writerow([rec.issue_id, rec.type, rec.severity, rec.status, *[round(v, 3) for v in rec.position_m],
                    rec.size, rec.first_seen.isoformat(), rec.last_seen.isoformat(), len(rec.observations),
                    rec.repair_suggested, rec.object.label if rec.object else "", last.image_url if last else ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{map_id}-issues.csv"'})


# ---------------------------------------------------------------- localize / inspect (README MVP)

@app.post("/localize", response_model=LocalizeOut, dependencies=[Auth])
def localize(
    s: SessionDep,
    image: Annotated[UploadFile, File()],
    metadata: Annotated[str, Form(description="LocalizeMetadata JSON")],
):
    """Image -> 6-DoF pose in the map (VPS)."""
    meta = _validated(LocalizeMetadata, metadata, "metadata")
    _map(s, meta.map_id)
    vps = vps_mod.get_vps()
    if vps is None or not vps.has_index(meta.map_id):
        raise HTTPException(503, "no VPS index for this map")
    frame = _frame(image, Intrinsics(**meta.intrinsics.model_dump()), meta.interface_orientation)
    r = vps.localize(meta.map_id, [frame])[0]
    if r is None:
        raise HTTPException(422, "image could not be localized")
    t, q = c2w_to_tq(r.c2w)
    return LocalizeOut(camera_pose={"t": t, "q": q}, camera_to_map=np.round(r.c2w, 6).tolist(),
                       confidence=r.confidence, num_inliers=r.num_inliers)


@app.post("/localize/multi", response_model=LocalizeMultiOut, dependencies=[Auth])
def localize_multi(
    s: SessionDep,
    images: Annotated[list[UploadFile], File(description="4-6 frames, ~0.5 s apart")],
    metadata: Annotated[str, Form(description="LocalizeMultiMetadata JSON")],
):
    """Several frames with their ARKit poses -> one consensus transform ARKit world -> map
    (the multi-frame query of hosted VPS services, self-hosted)."""
    meta = _validated(LocalizeMultiMetadata, metadata, "metadata")
    if len(images) != len(meta.frames):
        raise HTTPException(422, f"{len(images)} images but {len(meta.frames)} frame entries")
    _map(s, meta.map_id)
    vps = vps_mod.get_vps()
    if vps is None or not vps.has_index(meta.map_id):
        raise HTTPException(503, "no VPS index for this map")
    frames = [_frame(img, Intrinsics(**f.intrinsics.model_dump()), f.interface_orientation,
                     _pose(f.camera_to_world, meta.pose_convention))
              for img, f in zip(images, meta.frames)]
    res = vps.localize(meta.map_id, frames)
    ok = [(f, r) for f, r in zip(frames, res) if r is not None]
    if not ok:
        raise HTTPException(422, "no frame could be localized")
    al = alignment_from_frame_poses([f.c2w for f, _ in ok], [r.c2w for _, r in ok])
    conf = al["confidence"] * float(np.mean([r.confidence for _, r in ok]))
    room = pipeline.camera_room(s.get(Map, meta.map_id), {"matrix": np.asarray(al["matrix"]) @ frames[-1].c2w})
    return LocalizeMultiOut(
        device_to_map=al["matrix"], confidence=round(conf, 3), n_localized=len(ok), n_inliers=al["n_inliers"],
        room=room,
        frames=[FrameResult(index=i, localized=r is not None,
                            camera_to_map=np.round(r.c2w, 6).tolist() if r else None,
                            num_inliers=r.num_inliers if r else None) for i, r in enumerate(res)])


@app.post("/inspect", response_model=CaptureOut, dependencies=[Auth])
def inspect(
    s: SessionDep,
    image: Annotated[UploadFile, File(description="photo of the (possible) damage")],
    metadata: Annotated[str, Form(description="InspectMetadata JSON")],
):
    """localize -> detect -> lift to 3D -> match -> store, for one image. Synchronous (a few seconds)."""
    meta = _validated(InspectMetadata, metadata, "metadata")
    m = _map(s, meta.map_id)
    device = _pose(meta.camera_to_world, meta.pose_convention) if meta.camera_to_world else None
    hashes: list = []
    frame = _frame(image, Intrinsics(**meta.intrinsics.model_dump()), meta.interface_orientation, device, hashes)
    event_kind = meta.event_kind or _handover_kind(s, meta.handover_id, m.id)
    map_pose = None
    if meta.camera_to_map:
        map_pose = _pose(meta.camera_to_map, meta.pose_convention)
        if meta.interface_orientation == "portrait":
            map_pose = map_pose @ R_P
    cap = Capture(map_id=m.id, source_kind=meta.source.kind, device_id=meta.source.device_id,
                  device_model=meta.device_model, captured_at=meta.timestamp or now(), recorder=meta.recorder,
                  event_kind=event_kind, handover_id=meta.handover_id, original_sha256=hashes[0][0],
                  size_bytes=hashes[0][1])
    s.add(cap)
    s.commit()
    pipeline.inspect(cap.id, frame, [], map_pose, meta.pose_confidence)
    s.expire_all()
    return capture_out(s, _capture(s, cap.id))


@app.post("/detect", response_model=list[DetectionOut], dependencies=[Auth])
def detect(image: Annotated[UploadFile, File()]):
    """Detection only, nothing stored (live previews)."""
    img = _jpeg(image)
    w, h = Image.open(io.BytesIO(img)).size
    r = get_detector().detect(img, w, h)
    return [DetectionOut(id="", damage_class=d.damage_class, label=DAMAGE_CLASSES[d.damage_class]["label"],
                         prompt=d.prompt, confidence=d.confidence, bbox=d.bbox, polygons=d.polygons,
                         area_ratio=d.area_ratio, covers_full_frame=d.covers_full_frame, mask_url=None)
            for d in r.detections]


# ---------------------------------------------------------------- iOS app upload (docs/PLAN.md section 10)

APP_STATUS = {
    CaptureStatus.awaiting_upload: "uploaded", CaptureStatus.uploaded: "uploaded",
    CaptureStatus.processing: "processing", CaptureStatus.failed: "failed",
}


@app.post("/v1/captures", response_model=V1CaptureCreated, dependencies=[Auth])
def v1_create(body: V1CaptureCreate, s: SessionDep):
    """Step 1: get a signed upload URL for the capture zip. Idempotent per capture_id."""
    _map(s, body.map_id)
    cap = s.get(Capture, body.capture_id)
    if cap is None:
        cap = Capture(id=body.capture_id, map_id=body.map_id, status=CaptureStatus.awaiting_upload,
                      device_id=body.device_id, package_key=f"uploads/{body.capture_id}.zip",
                      recorder=body.recorder, handover_id=body.handover_id,
                      event_kind=body.event_kind or _handover_kind(s, body.handover_id, body.map_id))
        if body.created_unix:
            cap.captured_at = datetime.fromtimestamp(body.created_unix, UTC)
    elif cap.status in (CaptureStatus.processing,):
        raise HTTPException(409, "capture is being processed")
    else:
        cap.status = CaptureStatus.awaiting_upload
    s.add(cap)
    s.commit()
    url, expires = storage.upload_url(cap.package_key, "application/zip")
    return V1CaptureCreated(capture_id=cap.id, upload_url=url, expires_at=expires)


@app.put("/v1/uploads/{key:path}", status_code=200)
async def v1_local_upload(key: str, request: Request):
    """Local-storage stand-in for GCS signed URLs (capture zips, map splats / views)."""
    parts = key.split("/")
    allowed = (len(parts) == 2 and parts[0] == "uploads" and key.endswith(".zip")) or \
              (len(parts) == 3 and parts[0] == "maps" and parts[2] in ("splat.ply", "views.zip"))
    if not isinstance(storage, LocalStorage) or not allowed:
        raise HTTPException(404, "not found")
    path = storage.local_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with open(path, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            f.write(chunk)
    return {"size_bytes": size}


@app.post("/v1/captures/{capture_id}/complete", status_code=202, dependencies=[Auth])
def v1_complete(capture_id: str, s: SessionDep, bg: BackgroundTasks):
    """Step 3: the zip is uploaded; process it."""
    cap = _capture(s, capture_id)
    if not cap.package_key or not storage.exists(cap.package_key):
        raise HTTPException(409, "upload not found; PUT the zip to the upload_url first")
    cap.status = CaptureStatus.uploaded
    s.add(cap)
    s.commit()
    bg.add_task(pipeline.process_package, cap.id)
    return {"status": "processing"}


@app.get("/v1/captures/{capture_id}", response_model=V1CaptureStatus, dependencies=[Auth])
def v1_status(capture_id: str, s: SessionDep):
    cap = _capture(s, capture_id)
    return V1CaptureStatus(capture_id=cap.id, status=APP_STATUS.get(cap.status, "done"), detail=cap.status,
                           error=cap.error)


# ---------------------------------------------------------------- captures

@app.get("/captures", response_model=list[CaptureOut])
def list_captures(s: SessionDep, map_id: str | None = None, status: CaptureStatus | None = None,
                  has_damage: bool | None = None, limit: Annotated[int, Query(le=500)] = 50, offset: int = 0):
    q = select(Capture).order_by(Capture.created_at.desc())
    if map_id:
        q = q.where(Capture.map_id == map_id)
    if status:
        q = q.where(Capture.status == status)
    if has_damage is not None:
        q = q.where(Capture.has_damage == has_damage)
    return [capture_out(s, c) for c in s.exec(q.offset(offset).limit(limit)).all()]


@app.get("/captures/{capture_id}", response_model=CaptureOut)
def get_capture(capture_id: str, s: SessionDep):
    return capture_out(s, _capture(s, capture_id))


@app.delete("/captures/{capture_id}", dependencies=[Auth])
def delete_capture(capture_id: str, s: SessionDep):
    """Delete a capture with everything stored for it: the uploaded zip, photo, video frames, masks,
    overlay and SAM 3 results. Captures that filed records are refused: those records are part of the
    unit's hash chain (reject them in review instead)."""
    cap = _capture(s, capture_id)
    if cap.status in (CaptureStatus.uploaded, CaptureStatus.processing):
        raise HTTPException(409, "the capture is being processed; try again when it has finished")
    obs = s.exec(select(Observation).where(Observation.capture_id == cap.id)).all()
    if obs:
        issues = sorted({f"ISS-{o.issue_seq:04d}" for o in obs})
        raise HTTPException(409, f"the capture filed records on {', '.join(issues)}; they are part of the "
                                 "evidence log and cannot be deleted")
    # Keep the database row if storage fails, so a retry can finish partial cleanup.
    try:
        n_files = storage.delete_prefix(f"captures/{cap.id}/")
        if cap.package_key:
            n_files += int(storage.delete(cap.package_key))
    except Exception:
        logging.getLogger(__name__).exception("Storage deletion failed for capture %s", cap.id)
        raise HTTPException(503, "could not delete stored files; retry deletion") from None
    for d in s.exec(select(Detection).where(Detection.capture_id == cap.id)).all():
        s.delete(d)
    for i in s.exec(select(Issue).where(Issue.repair_suggested_capture_id == cap.id)).all():
        i.repair_suggested_capture_id = None
        s.add(i)
    s.flush()  # Remove dependent rows before the capture (foreign keys are enforced on Postgres).
    s.delete(cap)
    s.commit()
    return {"deleted": capture_id, "files": n_files}


@app.post("/captures/{capture_id}/registration", response_model=CaptureOut, status_code=202, dependencies=[Auth])
def set_registration(capture_id: str, body: RegistrationIn, s: SessionDep, bg: BackgroundTasks):
    """Deliver a localization later: external VPS results for frames, the photo pose, or the
    device->map transform. Damages are then lifted and matched."""
    cap = _capture(s, capture_id)
    if not cap.map_id:
        raise HTTPException(409, "capture has no map_id")
    given = [k for k in ("frame_poses", "camera_to_map", "device_to_map") if getattr(body, k) is not None]
    if len(given) != 1:
        raise HTTPException(422, "give exactly one of frame_poses, camera_to_map, device_to_map")
    pc = cap.photo_camera or {}
    K = {k: pc[k] for k in ("fx", "fy", "cx", "cy", "width", "height")} if "fx" in pc else pc.get("K")
    if not K:
        raise HTTPException(409, "capture has no photo intrinsics")
    device_photo = np.asarray(pc["matrix"]) if "matrix" in pc else None
    loc: dict
    if body.camera_to_map is not None:
        M = np.asarray(body.camera_to_map, dtype=np.float64)
        loc = {"method": "camera_to_map", "confidence": body.confidence if body.confidence is not None else 1.0}
    else:
        if device_photo is None:
            raise HTTPException(409, "capture has no device pose for the photo; send camera_to_map")
        if body.device_to_map is not None:
            T = np.asarray(body.device_to_map, dtype=np.float64)
            loc = {"method": "device_to_map", "confidence": body.confidence if body.confidence is not None else 1.0}
        else:
            frames = {f["name"]: f for f in cap.frames or []}
            try:
                pairs = [(np.asarray(frames[fp["frame"]]["c2w"]), np.asarray(fp["camera_to_map"])) for fp in body.frame_poses]
                al = alignment_from_frame_poses([a for a, _ in pairs], [b for _, b in pairs])
            except (KeyError, TypeError, ValueError) as e:
                raise HTTPException(422, f"invalid frame_poses: {e}")
            T = np.asarray(al["matrix"])
            loc = {"method": "frame_poses", "alignment": al,
                   "confidence": body.confidence if body.confidence is not None else al["confidence"]}
        M = T @ device_photo
    if M.shape != (4, 4):
        raise HTTPException(422, "poses must be 4x4")
    loc["camera"] = {"convention": "c2w_opencv", "matrix": M.round(6).tolist(), **K}
    bg.add_task(pipeline.relocalize, cap.id, loc)
    return capture_out(s, cap)


# ---------------------------------------------------------------- issues + review

@app.get("/issues/{issue_id}", response_model=IssueOut)
def get_issue(issue_id: str, s: SessionDep):
    return issue_out(s, _issue(s, issue_id))


@app.patch("/issues/{issue_id}", response_model=IssueOut, dependencies=[Auth])
def patch_issue(issue_id: str, body: IssuePatch, s: SessionDep):
    """Human decisions: confirm repaired, override severity, link to an object."""
    i = _issue(s, issue_id)
    if body.type is not None:
        if body.type not in DAMAGE_CLASSES:
            raise HTTPException(422, f"type must be one of {list(DAMAGE_CLASSES)}")
        i.damage_class, i.type_manual = body.type, True
    if body.status is not None:
        try:
            i.status = IssueStatus(body.status)
        except ValueError:
            raise HTTPException(422, f"status must be one of {[x.value for x in IssueStatus]}")
        closed = i.status in (IssueStatus.repaired, IssueStatus.rejected)
        i.closed_at = now() if closed else None
        i.closed_by = body.by if closed else None
        if i.status == IssueStatus.repaired:
            i.repair_suggested = False
    if body.severity is not None:
        if body.severity not in LEVELS:
            raise HTTPException(422, f"severity must be one of {LEVELS}")
        i.severity, i.severity_manual = body.severity, True
    if body.object_id is not None:
        i.object_id = body.object_id
    if body.object_label is not None:
        i.object_label = body.object_label
    s.add(i)
    s.commit()
    return issue_out(s, i)


@app.patch("/observations/{obs_id}", response_model=ObservationOut, dependencies=[Auth])
def review_observation(obs_id: str, body: ObservationReview, s: SessionDep):
    """Confirm or reject a finding. Rejected findings are training data for the detector; an issue
    whose observations are all rejected is closed as rejected."""
    try:
        o = s.get(Observation, int(obs_id.upper().removeprefix("OBS-")))
    except ValueError:
        o = None
    if o is None:
        raise HTTPException(404, "observation not found")
    if body.review not in ("confirmed", "rejected"):
        raise HTTPException(422, "review must be confirmed or rejected")
    o.review, o.reviewed_by, o.reviewed_at = body.review, body.reviewed_by, now()
    s.add(o)
    s.commit()
    i = s.get(Issue, o.issue_seq)
    reviews = [x.review for x in s.exec(select(Observation).where(Observation.issue_seq == i.seq)).all()]
    if all(r == "rejected" for r in reviews):
        i.status, i.closed_at = IssueStatus.rejected, now()
        s.add(i)
        s.commit()
    return observation_out(o)


# ---------------------------------------------------------------- units: details, dashboard, live view, evidence

def _handover_kind(s: Session, handover_id: str | None, map_id: str) -> str | None:
    if not handover_id:
        return None
    h = s.get(Handover, handover_id)
    if h is None or h.map_id != map_id:
        raise HTTPException(404, "handover not found for this map")
    if h.status != "open":
        raise HTTPException(409, "handover is finished")
    return h.kind


def _replace_issues(s: Session, m: Map) -> None:
    """Recompute room / surface / height of all issues (after rooms, north or floor changed)."""
    for i in s.exec(select(Issue).where(Issue.map_id == m.id)).all():
        pl = place(i.position, i.normal or None, up_axis=m.up_axis, north=m.north, floor_z=m.floor_z, rooms=m.rooms)
        i.room, i.surface, i.height_m, i.floor_xy = pl["room"], pl["surface"], pl["height_m"], pl["floor_xy"]
        s.add(i)


@app.patch("/maps/{map_id}", response_model=MapOut, dependencies=[Auth])
def patch_map(map_id: str, body: MapPatch, s: SessionDep):
    """Unit details and rooms (drawn on the floor plan). Issue places are recomputed."""
    m = _map(s, map_id)
    data = body.model_dump(exclude_unset=True)
    if "rooms" in data:
        data["rooms"] = [r.model_dump() for r in body.rooms or []]
    for k, v in data.items():
        setattr(m, k, v)
    s.add(m)
    _replace_issues(s, m)
    s.commit()
    return map_out(m)


@app.get("/maps/{map_id}/dashboard", response_model=DashboardOut)
def dashboard(map_id: str, s: SessionDep, latest: int = 5):
    """Numbers for the unit dashboard."""
    m = _map(s, map_id)
    issues = s.exec(select(Issue).where(Issue.map_id == map_id)).all()
    open_ = [i for i in issues if i.status in OPEN_STATUSES]
    repaired = [i for i in issues if i.status == IssueStatus.repaired and i.closed_at]
    year = now().year
    days = [(aware(i.closed_at) - aware(i.first_seen)).total_seconds() / 86400 for i in repaired]
    obs = s.exec(select(Observation).join(Issue, Observation.issue_seq == Issue.seq)
                 .where(Issue.map_id == map_id, Observation.review != "superseded")
                 .order_by(Observation.timestamp.desc())).all()
    months = []
    t = now()
    for k in range(11, -1, -1):
        y, mo = t.year, t.month - k
        while mo <= 0:
            y, mo = y - 1, mo + 12
        months.append(f"{y:04d}-{mo:02d}")
    counts = {mo: 0 for mo in months}
    for o in obs:
        key = f"{o.timestamp:%Y-%m}"
        if key in counts:
            counts[key] += 1
    by_seq = {i.seq: i for i in issues}
    return DashboardOut(
        open_findings=len(open_), closed_this_year=sum(aware(i.closed_at).year == year for i in repaired),
        days_to_repair_avg=round(sum(days) / len(days), 1) if days else None, records=len(obs),
        findings_by_status={"open": len(open_), "closed": len(repaired), "total": len(open_) + len(repaired)},
        records_per_month=[{"month": mo, "count": counts[mo]} for mo in months],
        latest_records=[LatestRecord(
            obs_id=o.obs_id, issue_id=f"ISS-{o.issue_seq:04d}", type=by_seq[o.issue_seq].damage_class,
            label=label(by_seq[o.issue_seq].damage_class), place=place_of(by_seq[o.issue_seq]),
            status=by_seq[o.issue_seq].status, event_kind=o.event_kind, recorder=o.recorder, timestamp=o.timestamp,
            thumbnail_url=storage.url(o.overlay_key or o.image_key) if (o.overlay_key or o.image_key) else None)
            for o in obs[:latest]],
        open_list=[OpenFinding(issue_id=i.issue_id, label=label(i.damage_class), place=place_of(i),
                               severity=i.severity, floor_plan_px=floor_plan_px(i, m))
                   for i in sorted(open_, key=lambda x: LEVELS.index(x.severity), reverse=True)])


@app.post("/maps/{map_id}/in-view", response_model=InViewOut, dependencies=[Auth])
def records_in_view(map_id: str, body: InViewIn, s: SessionDep):
    """Known open issues visible from a camera pose ('2 earlier records here'), with pixel positions
    for an AR overlay, plus the room the camera is in."""
    m = _map(s, map_id)
    if m.status != "ready":
        raise HTTPException(409, f"map is {m.status}")
    c2w = _pose(body.camera_to_map, "arkit" if body.pose_convention == "arkit" else "opencv")
    K = body.intrinsics
    cam = Camera.from_dict({"convention": "c2w_opencv", "matrix": c2w.tolist(), **K.model_dump()})
    xyz = np.load(storage.local_path(m.proj_key))["xyz"].astype(np.float64)
    vis = visible_points(xyz, cam)
    out = []
    for i in s.exec(select(Issue).where(Issue.map_id == map_id, Issue.status.in_(OPEN_STATUSES))).all():
        p = np.asarray(i.position)
        pc = cam.R @ p + cam.t
        dist = float(np.linalg.norm(p - cam.center))
        if pc[2] <= 0 or dist > body.max_distance_m:
            continue
        u, v = K.fx * pc[0] / pc[2] + K.cx, K.fy * pc[1] / pc[2] + K.cy
        if not (0 <= u < K.width and 0 <= v < K.height) or not is_visible(p, vis, xyz):
            continue
        obs = live_observations(s, i.seq)
        out.append(InViewRecord(issue_id=i.issue_id, type=i.damage_class, label=label(i.damage_class),
                                status=i.status, severity=i.severity, pixel=[round(u, 1), round(v, 1)],
                                distance_m=round(dist, 2), n_records=len(obs), last_seen=i.last_seen,
                                last_event_kind=obs[-1].event_kind if obs else None))
    return InViewOut(room=pipeline.camera_room(m, {"matrix": c2w}), records=out)


@app.get("/maps/{map_id}/verify")
def verify_records(map_id: str, s: SessionDep):
    """Check the unit's hash chain: every record's hash and link to the previous one."""
    _map(s, map_id)
    obs = s.exec(select(Observation).join(Issue, Observation.issue_seq == Issue.seq)
                 .where(Issue.map_id == map_id, Observation.record_hash.is_not(None))
                 .order_by(Observation.seq)).all()
    return evidence.verify_chain([(pipeline.hash_fields(o), o.prev_hash, o.record_hash) for o in obs])


@app.get("/maps/{map_id}/report.pdf")
def report_pdf(map_id: str, s: SessionDep, handover_id: str | None = None, include_closed: bool = False):
    """PDF report of the unit, or of the findings recorded during one handover."""
    m = _map(s, map_id)
    issues = _issues_query(s, map_id, None, None, None, include_closed, None)
    suffix = ""
    if handover_id:
        h = s.get(Handover, handover_id)
        if h is None or h.map_id != map_id:
            raise HTTPException(404, "handover not found")
        seqs = {o.issue_seq for o in s.exec(select(Observation).where(Observation.handover_id == h.id)).all()}
        issues = [i for i in _issues_query(s, map_id, None, None, None, True, None) if i.seq in seqs]
        suffix = f" · {h.kind.replace('_', '-')} {h.started_at:%d %b %Y}"
    pdf = report.build(map_out(m), m, [issue_out(s, i, m) for i in issues], dashboard(map_id, s), suffix)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{map_id}-report.pdf"'})


# ---------------------------------------------------------------- handovers (move-in / move-out)

@app.post("/maps/{map_id}/handovers", response_model=HandoverOut, status_code=201, dependencies=[Auth])
def start_handover(map_id: str, body: HandoverIn, s: SessionDep):
    """'Start handover': records made with this handover_id are linked to it."""
    _map(s, map_id)
    if body.kind not in ("move_in", "move_out"):
        raise HTTPException(422, "kind must be move_in or move_out")
    h = Handover(map_id=map_id, kind=body.kind, tenant=body.tenant, started_by=body.started_by, notes=body.notes)
    s.add(h)
    s.commit()
    return handover_out(s, h)


@app.get("/maps/{map_id}/handovers", response_model=list[HandoverOut])
def list_handovers(map_id: str, s: SessionDep):
    _map(s, map_id)
    hs = s.exec(select(Handover).where(Handover.map_id == map_id).order_by(Handover.started_at.desc())).all()
    return [handover_out(s, h) for h in hs]


@app.get("/handovers/{handover_id}", response_model=HandoverOut)
def get_handover(handover_id: str, s: SessionDep):
    h = s.get(Handover, handover_id)
    if h is None:
        raise HTTPException(404, "handover not found")
    return handover_out(s, h)


@app.post("/handovers/{handover_id}/finish", response_model=HandoverOut, dependencies=[Auth])
def finish_handover(handover_id: str, s: SessionDep):
    h = s.get(Handover, handover_id)
    if h is None:
        raise HTTPException(404, "handover not found")
    h.status, h.finished_at = "finished", now()
    s.add(h)
    s.commit()
    return handover_out(s, h)


# ---------------------------------------------------------------- files (local storage only)

@app.get("/files/{key:path}")
def get_file(key: str):
    if not isinstance(storage, LocalStorage):
        raise HTTPException(404, "not found")
    try:
        path = storage.local_path(key)
    except ValueError:
        raise HTTPException(400, "invalid key")
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path)


# viewer prototype adapter (registered last: it imports from this module)
from .viewer_api import router as viewer_router  # noqa: E402

app.include_router(viewer_router)
