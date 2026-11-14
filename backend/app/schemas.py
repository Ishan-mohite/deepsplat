"""API request/response models — this is the contract for the app, frontend and splat team."""

from datetime import datetime

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .detection import DAMAGE_CLASSES
from .models import Capture, Damage3D, Detection, ReconstructionJob, Scene
from .storage import storage


def _url(key: str | None) -> str | None:
    return storage.url(key) if key else None


# ---------- upload ----------

class Location(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    accuracy_m: float | None = None
    heading_deg: float | None = None


class ArkitCamera(BaseModel):
    """ARKit pose + intrinsics of the photo, taken from the same ARFrame as the image."""
    matrix: list[list[float]] = Field(
        description="ARFrame.camera.transform (camera->world) as 4 rows. simd is column-major: "
                    "row i = [t.columns.0[i], t.columns.1[i], t.columns.2[i], t.columns.3[i]]")
    fx: float = Field(description="ARFrame.camera.intrinsics.columns.0.x")
    fy: float = Field(description="ARFrame.camera.intrinsics.columns.1.y")
    cx: float = Field(description="ARFrame.camera.intrinsics.columns.2.x")
    cy: float = Field(description="ARFrame.camera.intrinsics.columns.2.y")
    width: int = Field(description="ARFrame.camera.imageResolution.width")
    height: int = Field(description="ARFrame.camera.imageResolution.height")
    timestamp: float | None = Field(None, description="ARFrame.timestamp")
    tracking_state: str | None = Field(None, examples=["normal", "limited"])

    def as_camera_dict(self) -> dict:
        return {"convention": "arkit", **self.model_dump(exclude_none=True)}


class SceneHint(BaseModel):
    """Rough position in the building scan (scan coordinates, meters) to speed up / stabilize alignment."""
    center: list[float] = Field(min_length=3, max_length=3)
    radius: float = Field(4.0, gt=0)


class CaptureMetadata(BaseModel):
    """JSON sent as the `metadata` form field of POST /captures. Everything is optional."""
    arkit: ArkitCamera | None = None
    arkit_session_id: str | None = Field(None, description="same id for all captures of one ARSession -> aligned once")
    scene_id: str | None = Field(None, description="building scan this capture belongs to")
    scene_hint: SceneHint | None = None
    source_type: str = Field("phone", description='"phone" or "drone"')
    device_model: str | None = Field(None, examples=["iPhone 15 Pro"])
    device_os: str | None = Field(None, examples=["iOS 19.1"])
    app_version: str | None = None
    captured_at: datetime | None = None
    location: Location | None = None
    context: dict = Field(default_factory=dict, description="free-form: building, floor, room, notes, ...")


# ---------- responses ----------

class MediaOut(BaseModel):
    url: str
    content_type: str | None = None
    width: int | None = None
    height: int | None = None


class ImageOut(MediaOut):
    sha256: str | None = None
    camera: dict = Field(default_factory=dict, description='ARKit pose + intrinsics of the photo')


class VideoOut(MediaOut):
    size_bytes: int | None = None
    duration_s: float | None = None
    fps: float | None = None


class DetectionOut(BaseModel):
    id: str
    damage_class: str
    label: str
    prompt: str
    confidence: float
    bbox: list[float] = Field(description="[x0, y0, x1, y1] in image pixels")
    polygons: list[list[list[float]]] = Field(description="mask polygons [[[x, y], ...], ...] in image pixels")
    area_px: float
    area_ratio: float
    covers_full_frame: bool


class DetectionSummary(BaseModel):
    has_damage: bool | None
    classes: list[str]
    max_confidence: float | None
    count: int
    model: str | None
    latency_s: float | None
    detected_at: datetime | None
    overlay_url: str | None


class Damage3DOut(BaseModel):
    id: str
    detection_id: str
    capture_id: str
    frame: str = Field(description='"scene" = building scan coordinates (m), "splat" = per-capture splat')
    scene_id: str | None
    job_id: str | None
    damage_class: str
    label: str
    confidence: float
    centroid: list[float]
    bbox_min: list[float]
    bbox_max: list[float]
    normal: list[float]
    n_points: int
    mean_depth: float | None
    area_est: float | None = Field(description='approx. damage area in scene units² (m² for ARKit/metric scenes)')
    indices_url: str | None = Field(description="splat only: JSON list of splat vertex indices inside the damage")
    points_url: str | None = Field(description="JSON list of [x, y, z] inside the damage (max 5000)")
    method: str


class JobOut(BaseModel):
    id: str
    capture_id: str
    status: str
    created_at: datetime
    claimed_at: datetime | None
    claimed_by: str | None
    completed_at: datetime | None
    error: str | None
    video_url: str | None
    image_url: str
    image_width: int | None
    image_height: int | None
    photo_camera: dict = Field(description='ARKit pose of the photo (if the app sent one)')
    arkit_frames_url: str | None = Field(description='per-frame ARKit poses of the video (if sent)')
    splat_url: str | None
    splat_format: str | None
    camera: dict
    scene_info: dict


class SplatOut(BaseModel):
    job_id: str
    status: str
    splat_url: str | None
    splat_format: str | None
    camera: dict
    scene_info: dict


class SceneOut(BaseModel):
    """Building scan."""
    id: str
    name: str
    status: str
    error: str | None
    created_at: datetime
    up_axis: str
    units: str
    context: dict
    cloud_url: str = Field(description="original upload")
    preview_url: str | None = Field(description="downsampled .ply (3 cm) for the frontend")
    stats: dict


class RegistrationOut(BaseModel):
    ok: bool | None = None
    method: str | None = None
    fitness: float | None = None
    rmse: float | None = None
    max_tilt_deg: float | None = None
    ambiguous: bool | None = None
    n_candidates: int | None = None
    matrix: list[list[float]] | None = Field(None, description="ARKit world -> scene, 4x4 rows")
    photo_camera_scene: dict | None = Field(None, description="photo pose in scene coordinates (c2w_opengl)")
    registered_at: str | None = None
    source_capture_id: str | None = None


class CaptureOut(BaseModel):
    id: str
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime
    source_type: str
    device_model: str | None
    device_os: str | None
    app_version: str | None
    captured_at: datetime | None
    location: Location
    context: dict
    image: ImageOut
    video: VideoOut | None
    detection: DetectionSummary
    detections: list[DetectionOut]
    scene_id: str | None
    arkit_session_id: str | None
    geometry_url: str | None
    registration: RegistrationOut
    splat: SplatOut | None = Field(description='per-capture splat reconstruction job')
    damages_3d: list[Damage3DOut]


class CaptureListItem(BaseModel):
    id: str
    status: str
    created_at: datetime
    source_type: str
    location: Location
    context: dict
    has_damage: bool | None
    classes: list[str]
    max_confidence: float | None
    image_url: str
    overlay_url: str | None


class DetectFrameOut(BaseModel):
    has_damage: bool
    model: str
    latency_s: float
    width: int
    height: int
    detections: list[DetectionOut]


# ---------- builders ----------

def _label(cls: str) -> str:
    return DAMAGE_CLASSES.get(cls, {}).get("label", cls)


def detection_out(d) -> DetectionOut:
    return DetectionOut(
        id=getattr(d, "id", "") or "", damage_class=d.damage_class, label=_label(d.damage_class),
        prompt=d.prompt, confidence=d.confidence, bbox=d.bbox, polygons=d.polygons,
        area_px=d.area_px, area_ratio=d.area_ratio, covers_full_frame=d.covers_full_frame)


def _location(c: Capture) -> Location:
    return Location(latitude=c.latitude, longitude=c.longitude, altitude_m=c.altitude_m,
                    accuracy_m=c.gps_accuracy_m, heading_deg=c.heading_deg)


def _latest_job(s: Session, capture_id: str) -> ReconstructionJob | None:
    return s.exec(select(ReconstructionJob).where(ReconstructionJob.capture_id == capture_id)
                  .order_by(ReconstructionJob.created_at.desc())).first()


def job_out(s: Session, j: ReconstructionJob) -> JobOut:
    c = s.get(Capture, j.capture_id)
    return JobOut(
        id=j.id, capture_id=j.capture_id, status=j.status, created_at=j.created_at,
        claimed_at=j.claimed_at, claimed_by=j.claimed_by, completed_at=j.completed_at, error=j.error,
        video_url=_url(c.video_key), image_url=_url(c.image_key),
        image_width=c.image_width, image_height=c.image_height,
        photo_camera=c.photo_camera, arkit_frames_url=_url(c.arkit_frames_key),
        splat_url=_url(j.splat_key), splat_format=j.splat_format, camera=j.camera, scene_info=j.scene_info)


def capture_out(s: Session, c: Capture) -> CaptureOut:
    dets = s.exec(select(Detection).where(Detection.capture_id == c.id)
                  .order_by(Detection.confidence.desc())).all()
    d3 = s.exec(select(Damage3D).where(Damage3D.capture_id == c.id)).all()
    job = _latest_job(s, c.id)
    return CaptureOut(
        id=c.id, status=c.status, error=c.error, created_at=c.created_at, updated_at=c.updated_at,
        source_type=c.source_type, device_model=c.device_model, device_os=c.device_os,
        app_version=c.app_version, captured_at=c.captured_at, location=_location(c), context=c.context,
        image=ImageOut(url=_url(c.image_key), content_type="image/jpeg", width=c.image_width,
                       height=c.image_height, sha256=c.image_sha256, camera=c.photo_camera),
        video=VideoOut(url=_url(c.video_key), content_type=c.video_content_type,
                       width=c.video_width, height=c.video_height, size_bytes=c.video_size_bytes,
                       duration_s=c.video_duration_s, fps=c.video_fps) if c.video_key else None,
        detection=DetectionSummary(
            has_damage=c.has_damage, classes=sorted({d.damage_class for d in dets}),
            max_confidence=max((d.confidence for d in dets), default=None), count=len(dets),
            model=c.detector_model, latency_s=c.detection_latency_s, detected_at=c.detected_at,
            overlay_url=_url(c.overlay_key)),
        detections=[detection_out(d) for d in dets],
        scene_id=c.scene_id, arkit_session_id=c.arkit_session_id, geometry_url=_url(c.geometry_key),
        registration=RegistrationOut(**{k: v for k, v in (c.registration or {}).items()
                                         if k in RegistrationOut.model_fields}),
        splat=SplatOut(job_id=job.id, status=job.status, splat_url=_url(job.splat_key),
                       splat_format=job.splat_format, camera=job.camera,
                       scene_info=job.scene_info) if job else None,
        damages_3d=[damage3d_out(x) for x in d3],
    )


def damage3d_out(x: Damage3D) -> Damage3DOut:
    return Damage3DOut(
        id=x.id, detection_id=x.detection_id, capture_id=x.capture_id, frame=x.frame,
        scene_id=x.scene_id, job_id=x.job_id, damage_class=x.damage_class, label=_label(x.damage_class),
        confidence=x.confidence, centroid=x.centroid, bbox_min=x.bbox_min, bbox_max=x.bbox_max,
        normal=x.normal, n_points=x.n_points, mean_depth=x.mean_depth, area_est=x.area_est,
        indices_url=_url(x.indices_key), points_url=_url(x.points_key), method=x.method)


def scene_out(sc: Scene) -> "SceneOut":
    return SceneOut(id=sc.id, name=sc.name, status=sc.status, error=sc.error, created_at=sc.created_at,
                     up_axis=sc.up_axis, units=sc.units, context=sc.context, cloud_url=_url(sc.cloud_key),
                     preview_url=_url(sc.preview_key), stats=sc.stats)


def capture_list_item(s: Session, c: Capture) -> CaptureListItem:
    dets = s.exec(select(Detection).where(Detection.capture_id == c.id)).all()
    return CaptureListItem(
        id=c.id, status=c.status, created_at=c.created_at, source_type=c.source_type,
        location=_location(c), context=c.context, has_damage=c.has_damage,
        classes=sorted({d.damage_class for d in dets}),
        max_confidence=max((d.confidence for d in dets), default=None),
        image_url=_url(c.image_key), overlay_url=_url(c.overlay_key))
