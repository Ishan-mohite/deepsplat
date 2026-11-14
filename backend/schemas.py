"""API contract for the iOS app, robots / glasses, the digital-twin viewer and external VPS.

Conventions
* Map coordinates: meters, the splat's own frame.
* `camera_pose`: {"t": camera position, "q": camera->map rotation quaternion [x, y, z, w]},
  camera axes OpenCV (x right, y down, z forward) for the upright image.
* 4x4 matrices are row-major nested lists.
"""

from datetime import datetime

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from inspection.classes import label
from localization.cameras import c2w_to_tq

from .models import Capture, Detection, Issue, Map, Observation
from .storage import storage


def _url(key: str | None) -> str | None:
    return storage.url(key) if key else None


# ---------------------------------------------------------------- inputs

class IntrinsicsIn(BaseModel):
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int


class SourceIn(BaseModel):
    kind: str = Field("phone", description="phone | glasses | robot | fixed")
    device_id: str | None = None


class InspectMetadata(BaseModel):
    """`metadata` form field of POST /inspect (one image, e.g. from a robot or smart glasses)."""
    map_id: str
    intrinsics: IntrinsicsIn = Field(description="for the image as uploaded")
    interface_orientation: str = Field("landscape", description='"portrait" = raw sensor image, rotate (iOS)')
    camera_to_map: list | None = Field(None, description="device already localized: camera->map pose, 16 numbers or 4x4")
    camera_to_world: list | None = Field(None, description="device tracking pose (ARKit), kept for later alignment")
    pose_convention: str = Field("arkit", description='axes of the poses: "arkit" (x right, y up, -z fwd) or "opencv"')
    pose_confidence: float | None = None
    source: SourceIn = SourceIn()
    timestamp: datetime | None = None
    device_model: str | None = None


class LocalizeMetadata(BaseModel):
    map_id: str
    intrinsics: IntrinsicsIn
    interface_orientation: str = "landscape"


class FrameIn(BaseModel):
    intrinsics: IntrinsicsIn
    camera_to_world: list = Field(description="device tracking pose (ARKit camera.transform), 16 numbers or 4x4")
    interface_orientation: str = "portrait"


class LocalizeMultiMetadata(BaseModel):
    """`metadata` of POST /localize/multi: one entry per uploaded image, same order (like MultiSet's
    multi-image query)."""
    map_id: str
    frames: list[FrameIn] = Field(min_length=1, max_length=10)
    pose_convention: str = "arkit"


class V1CaptureCreate(BaseModel):
    """iOS app, step 1 (docs/PLAN.md section 10)."""
    capture_id: str
    map_id: str
    size_bytes: int | None = None
    created_unix: float | None = None
    device_id: str | None = None


class RegistrationIn(BaseModel):
    """Localization delivered later (external VPS, manual alignment). Give exactly one of:

    * `frame_poses`: [{"frame": "<name from capture.frames>", "camera_to_map": 4x4}] (OpenCV axes,
      upright frame as served by the backend); carried over to the photo via the device poses
    * `camera_to_map`: 4x4 pose of the photo itself (OpenCV axes, upright photo)
    * `device_to_map`: 4x4 transform device tracking world -> map
    """
    frame_poses: list[dict] | None = None
    camera_to_map: list[list[float]] | None = None
    device_to_map: list[list[float]] | None = None
    confidence: float | None = Field(None, description="override; default from frame consistency or 1.0")


class IssuePatch(BaseModel):
    status: str | None = Field(None, description="new | worsening | stable | repaired | rejected")
    severity: str | None = Field(None, description="low | medium | high | critical (sets manual severity)")
    object_id: str | None = None
    object_label: str | None = None


class ObservationReview(BaseModel):
    review: str = Field(description="confirmed | rejected")
    reviewed_by: str


# ---------------------------------------------------------------- outputs

class CameraPose(BaseModel):
    t: list[float]
    q: list[float]


class SourceOut(BaseModel):
    kind: str
    device_id: str | None


class ObservationOut(BaseModel):
    obs_id: str
    issue_id: str
    capture_id: str
    timestamp: datetime
    source: SourceOut
    camera_pose: CameraPose
    pose_confidence: float | None
    image_url: str | None
    mask_url: str | None
    overlay_url: str | None
    length_m: float | None
    area_m2: float | None
    detector_confidence: float
    position_m: list[float]
    change: str = Field(description="new | worsening | stable")
    review: str
    reviewed_by: str | None
    points_url: str | None = Field(description="JSON [[x,y,z], ...] on the surface (max 5000)")
    gaussians_url: str | None = Field(description="JSON list of gaussian indices in the map splat")


class ObjectOut(BaseModel):
    id: str | None
    label: str | None


class IssueOut(BaseModel):
    """The record from the project README, step 6."""
    issue_id: str
    map_id: str
    status: str
    type: str
    label: str
    severity: str
    object: ObjectOut | None
    position_m: list[float]
    normal: list[float]
    size: float | None = Field(description="last length (m, cracks) or area (m²)")
    first_seen: datetime
    last_seen: datetime
    repair_suggested: bool
    repair_suggested_capture_id: str | None
    observations: list[ObservationOut]


class DetectionOut(BaseModel):
    id: str
    damage_class: str
    label: str
    prompt: str
    confidence: float
    bbox: list[float]
    polygons: list[list[list[float]]]
    area_ratio: float
    covers_full_frame: bool
    mask_url: str | None


class LocalizationOut(BaseModel):
    method: str | None = Field(None, description="client | vps_frames | vps_photo | frame_poses | camera_to_map | device_to_map")
    confidence: float | None = None
    camera_pose: CameraPose | None = None
    camera_to_map: list[list[float]] | None = None
    error: str | None = None
    details: dict = {}


class FrameOut(BaseModel):
    name: str
    image_url: str
    intrinsics: dict
    camera_to_world: list[list[float]] | None = Field(description="device tracking pose, OpenCV axes, upright image")
    t: float | None


class CaptureOut(BaseModel):
    id: str
    map_id: str | None
    status: str
    error: str | None
    created_at: datetime
    captured_at: datetime | None
    source: SourceOut
    device_model: str | None
    image_url: str | None
    overlay_url: str | None
    image_size: list[int] | None
    quality: dict
    localization: LocalizationOut
    has_damage: bool | None
    detector_model: str | None
    detection_latency_s: float | None
    detections: list[DetectionOut]
    observations: list[ObservationOut]
    frames: list[FrameOut]


class MapOut(BaseModel):
    id: str
    name: str
    status: str
    error: str | None
    created_at: datetime
    up_axis: str
    context: dict
    splat_url: str = Field(description="the trained splat (.ply), load this in the viewer")
    vps_status: str
    vps_info: dict
    stats: dict


class LocalizeOut(BaseModel):
    camera_pose: CameraPose
    camera_to_map: list[list[float]]
    confidence: float
    num_inliers: int


class FrameResult(BaseModel):
    index: int
    localized: bool
    camera_to_map: list[list[float]] | None = None
    num_inliers: int | None = None


class LocalizeMultiOut(BaseModel):
    device_to_map: list[list[float]] = Field(description="ARKit world -> map; apply to any ARKit pose")
    confidence: float
    n_localized: int
    n_inliers: int = Field(description="frames consistent with the consensus")
    frames: list[FrameResult]


class V1CaptureCreated(BaseModel):
    capture_id: str
    upload_url: str
    expires_at: datetime


class V1CaptureStatus(BaseModel):
    capture_id: str
    status: str = Field(description="uploaded | processing | done | failed (app view)")
    detail: str = Field(description="backend status, e.g. needs_localization")
    error: str | None = None


# ---------------------------------------------------------------- builders

def observation_out(o: Observation) -> ObservationOut:
    return ObservationOut(
        obs_id=o.obs_id, issue_id=f"ISS-{o.issue_seq:04d}", capture_id=o.capture_id, timestamp=o.timestamp,
        source=SourceOut(kind=o.source_kind, device_id=o.device_id),
        camera_pose=CameraPose(t=o.camera_t, q=o.camera_q), pose_confidence=o.pose_confidence,
        image_url=_url(o.image_key), mask_url=_url(o.mask_key), overlay_url=_url(o.overlay_key),
        length_m=o.length_m, area_m2=o.area_m2, detector_confidence=o.detector_confidence,
        position_m=o.position, change=o.change, review=o.review, reviewed_by=o.reviewed_by,
        points_url=_url(o.points_key), gaussians_url=_url(o.indices_key))


def issue_out(s: Session, i: Issue) -> IssueOut:
    obs = s.exec(select(Observation).where(Observation.issue_seq == i.seq).order_by(Observation.timestamp)).all()
    return IssueOut(
        issue_id=i.issue_id, map_id=i.map_id, status=i.status, type=i.damage_class, label=label(i.damage_class),
        severity=i.severity,
        object=ObjectOut(id=i.object_id, label=i.object_label) if (i.object_id or i.object_label) else None,
        position_m=i.position, normal=i.normal, size=i.size, first_seen=i.first_seen, last_seen=i.last_seen,
        repair_suggested=i.repair_suggested, repair_suggested_capture_id=i.repair_suggested_capture_id,
        observations=[observation_out(o) for o in obs])


def localization_out(loc: dict) -> LocalizationOut:
    cam = (loc or {}).get("camera")
    pose = None
    if cam:
        t, q = c2w_to_tq(cam["matrix"])
        pose = CameraPose(t=t, q=q)
    details = {k: v for k, v in (loc or {}).items() if k not in ("method", "confidence", "camera", "error")}
    return LocalizationOut(method=(loc or {}).get("method"), confidence=(loc or {}).get("confidence"),
                           camera_pose=pose, camera_to_map=cam["matrix"] if cam else None,
                           error=(loc or {}).get("error"), details=details)


def capture_out(s: Session, c: Capture) -> CaptureOut:
    dets = s.exec(select(Detection).where(Detection.capture_id == c.id).order_by(Detection.confidence.desc())).all()
    obs = s.exec(select(Observation).where(Observation.capture_id == c.id)).all()
    return CaptureOut(
        id=c.id, map_id=c.map_id, status=c.status, error=c.error, created_at=c.created_at,
        captured_at=c.captured_at, source=SourceOut(kind=c.source_kind, device_id=c.device_id),
        device_model=c.device_model, image_url=_url(c.image_key), overlay_url=_url(c.overlay_key),
        image_size=[c.image_width, c.image_height] if c.image_width else None, quality=c.quality,
        localization=localization_out(c.localization), has_damage=c.has_damage,
        detector_model=c.detector_model, detection_latency_s=c.detection_latency_s,
        detections=[DetectionOut(id=d.id, damage_class=d.damage_class, label=label(d.damage_class), prompt=d.prompt,
                                 confidence=d.confidence, bbox=d.bbox, polygons=d.polygons, area_ratio=d.area_ratio,
                                 covers_full_frame=d.covers_full_frame, mask_url=_url(d.mask_key)) for d in dets],
        observations=[observation_out(o) for o in obs],
        frames=[FrameOut(name=f["name"], image_url=_url(f["image_key"]), intrinsics=f["K"],
                         camera_to_world=f.get("c2w"), t=f.get("t")) for f in (c.frames or [])])


def map_out(m: Map) -> MapOut:
    return MapOut(id=m.id, name=m.name, status=m.status, error=m.error, created_at=m.created_at, up_axis=m.up_axis,
                  context=m.context, splat_url=_url(m.splat_key), vps_status=m.vps_status, vps_info=m.vps_info,
                  stats=m.stats)
