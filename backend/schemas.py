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
from inspection.floorplan import to_px
from inspection.placement import describe
from localization.cameras import c2w_to_tq

from .models import Capture, Detection, Handover, Issue, Map, Observation, aware
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
    recorder: str | None = Field(None, examples=["M. Keller"])
    event_kind: str | None = Field(None, description="move_in | move_out | repair | tenant_report | inspection")
    handover_id: str | None = None


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
    recorder: str | None = Field(None, examples=["M. Keller"])
    event_kind: str | None = Field(None, description="move_in | move_out | repair | tenant_report | inspection")
    handover_id: str | None = None


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
    type: str | None = Field(None, description="correct the damage type (e.g. crack -> scratch)")
    status: str | None = Field(None, description="new | worsening | stable | repaired | rejected")
    severity: str | None = Field(None, description="low | medium | high | critical (sets manual severity)")
    object_id: str | None = None
    object_label: str | None = None
    by: str | None = Field(None, description="who made the change (e.g. who confirmed the repair)")


class Room(BaseModel):
    name: str = Field(examples=["Bath"])
    polygon: list[list[float]] = Field(description="[[x, y], ...] in floor-plane map coordinates (m)")
    floor_z: float | None = None


class MapPatch(BaseModel):
    """Unit details; rooms are usually drawn on the floor plan once after the scan."""
    name: str | None = None
    property: str | None = None
    floor_label: str | None = None
    scanned_at: datetime | None = None
    north: list[float] | None = Field(None, description="direction of north in the floor plane, e.g. [0, 1]")
    floor_z: float | None = None
    rooms: list[Room] | None = None


class HandoverIn(BaseModel):
    kind: str = Field("move_in", description="move_in | move_out")
    tenant: str | None = None
    started_by: str | None = None
    notes: str | None = None


class InViewIn(BaseModel):
    """Live camera: which known records are in view? (AR hint '2 earlier records here')"""
    camera_to_map: list = Field(description="camera->map pose, 16 numbers or 4x4")
    intrinsics: IntrinsicsIn
    pose_convention: str = Field("opencv", description='"opencv" or "arkit" camera axes')
    max_distance_m: float = 5.0


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
    detected_type: str
    recorder: str | None
    event_kind: str | None
    handover_id: str | None
    recorded_at: datetime = Field(description="server time the record was saved")
    image_sha256: str | None = Field(description="SHA-256 of the photo at image_url")
    record_hash: str | None = Field(description="SHA-256 over this record + prev_hash (hash chain per unit)")
    prev_hash: str | None
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
    room: str | None
    surface: str | None = Field(description="floor | ceiling | north wall | east wall | ...")
    height_m: float | None = Field(description="above floor")
    place: str = Field(description='e.g. "Bath · north wall"')
    floor_xy: list[float]
    floor_plan_px: list[float] | None = Field(description="pin position on the floor plan image")
    type_corrected: bool
    size: float | None = Field(description="last length (m, cracks/scratches) or area (m²)")
    first_seen: datetime
    last_seen: datetime
    closed_at: datetime | None
    closed_by: str | None
    days_to_repair: float | None
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
    room: str | None = Field(None, description='where the camera is, e.g. "Bathroom" ("Localized · Bathroom")')
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
    recorder: str | None
    event_kind: str | None
    handover_id: str | None
    original_sha256: str | None
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
    """A unit: its trained splat, floor plan and rooms."""
    id: str
    name: str
    property: str | None
    floor_label: str | None
    scanned_at: datetime | None
    status: str
    error: str | None
    created_at: datetime
    up_axis: str
    north: list[float]
    floor_z: float | None
    rooms: list[dict]
    floor_plan_url: str | None = Field(description="top-down image rendered from the splat")
    floor_plan: dict = Field(description="px = (x - origin_x) / m_per_px, py = (origin_y - y) / m_per_px")
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
    room: str | None = Field(None, description="room of the last frame's camera")
    frames: list[FrameResult]


class InViewRecord(BaseModel):
    issue_id: str
    type: str
    label: str
    status: str
    severity: str
    pixel: list[float] = Field(description="[u, v] in the given image")
    distance_m: float
    n_records: int
    last_seen: datetime
    last_event_kind: str | None


class InViewOut(BaseModel):
    room: str | None
    records: list[InViewRecord]


class HandoverOut(BaseModel):
    id: str
    map_id: str
    kind: str
    tenant: str | None
    started_by: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    notes: str | None
    n_records: int
    issue_ids: list[str] = Field(description="issues recorded during this handover")
    new_issue_ids: list[str] = Field(description="first recorded during this handover")
    previous_handover_id: str | None
    new_since_previous: list[str] = Field(description="open issues first seen after the previous handover")


class LatestRecord(BaseModel):
    obs_id: str
    issue_id: str
    type: str
    label: str
    place: str
    status: str
    event_kind: str | None
    recorder: str | None
    timestamp: datetime
    thumbnail_url: str | None


class OpenFinding(BaseModel):
    issue_id: str
    label: str
    place: str
    severity: str
    floor_plan_px: list[float] | None


class DashboardOut(BaseModel):
    open_findings: int
    closed_this_year: int
    days_to_repair_avg: float | None
    records: int
    findings_by_status: dict = Field(description="{open, closed, total}")
    records_per_month: list[dict] = Field(description="[{month: 'YYYY-MM', count}] last 12 months")
    latest_records: list[LatestRecord]
    open_list: list[OpenFinding]


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
        detected_type=o.damage_class, recorder=o.recorder, event_kind=o.event_kind, handover_id=o.handover_id,
        recorded_at=o.recorded_at, image_sha256=o.image_sha256, record_hash=o.record_hash, prev_hash=o.prev_hash,
        points_url=_url(o.points_key), gaussians_url=_url(o.indices_key))


def live_observations(s: Session, issue_seq: int) -> list[Observation]:
    return s.exec(select(Observation).where(Observation.issue_seq == issue_seq, Observation.review != "superseded")
                  .order_by(Observation.timestamp)).all()


def place_of(i: Issue) -> str:
    return describe({"room": i.room, "surface": i.surface})


def floor_plan_px(i: Issue, m: Map | None) -> list[float] | None:
    if m is None or not m.floor_plan or not i.floor_xy:
        return None
    return to_px(i.floor_xy, m.floor_plan)


def issue_out(s: Session, i: Issue, m: Map | None = None) -> IssueOut:
    m = m or s.get(Map, i.map_id)
    obs = live_observations(s, i.seq)
    days = None
    if i.closed_at and i.status == "repaired":
        days = round((aware(i.closed_at) - aware(i.first_seen)).total_seconds() / 86400, 1)
    return IssueOut(
        issue_id=i.issue_id, map_id=i.map_id, status=i.status, type=i.damage_class, label=label(i.damage_class),
        severity=i.severity,
        object=ObjectOut(id=i.object_id, label=i.object_label) if (i.object_id or i.object_label) else None,
        position_m=i.position, normal=i.normal, room=i.room, surface=i.surface, height_m=i.height_m,
        place=place_of(i), floor_xy=i.floor_xy or [], floor_plan_px=floor_plan_px(i, m),
        type_corrected=i.type_manual, size=i.size, first_seen=i.first_seen, last_seen=i.last_seen,
        closed_at=i.closed_at, closed_by=i.closed_by, days_to_repair=days,
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
                           room=(loc or {}).get("room"), error=(loc or {}).get("error"),
                           details={k: v for k, v in details.items() if k != "room"})


def capture_out(s: Session, c: Capture) -> CaptureOut:
    dets = s.exec(select(Detection).where(Detection.capture_id == c.id).order_by(Detection.confidence.desc())).all()
    obs = s.exec(select(Observation).where(Observation.capture_id == c.id, Observation.review != "superseded")).all()
    return CaptureOut(
        id=c.id, map_id=c.map_id, status=c.status, error=c.error, created_at=c.created_at,
        captured_at=c.captured_at, source=SourceOut(kind=c.source_kind, device_id=c.device_id),
        recorder=c.recorder, event_kind=c.event_kind, handover_id=c.handover_id, original_sha256=c.original_sha256,
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
    return MapOut(id=m.id, name=m.name, property=m.property, floor_label=m.floor_label, scanned_at=m.scanned_at,
                  status=m.status, error=m.error, created_at=m.created_at, up_axis=m.up_axis, north=m.north,
                  floor_z=m.floor_z, rooms=m.rooms, floor_plan_url=_url(m.floor_plan_key), floor_plan=m.floor_plan,
                  context=m.context, splat_url=_url(m.splat_key), vps_status=m.vps_status, vps_info=m.vps_info,
                  stats=m.stats)


def handover_out(s: Session, h: Handover) -> HandoverOut:
    obs = s.exec(select(Observation).where(Observation.handover_id == h.id, Observation.review != "superseded")).all()
    issue_seqs = sorted({o.issue_seq for o in obs})
    new = sorted({o.issue_seq for o in obs if o.change == "new"})
    prev = s.exec(select(Handover).where(Handover.map_id == h.map_id, Handover.started_at < h.started_at)
                  .order_by(Handover.started_at.desc())).first()
    since = []
    if prev is not None:
        since = [i.issue_id for i in s.exec(select(Issue).where(Issue.map_id == h.map_id,
                                                                Issue.status.in_(("new", "worsening", "stable")),
                                                                Issue.first_seen > prev.started_at)).all()]
    return HandoverOut(id=h.id, map_id=h.map_id, kind=h.kind, tenant=h.tenant, started_by=h.started_by,
                       started_at=h.started_at, finished_at=h.finished_at, status=h.status, notes=h.notes,
                       n_records=len(obs), issue_ids=[f"ISS-{x:04d}" for x in issue_seqs],
                       new_issue_ids=[f"ISS-{x:04d}" for x in new],
                       previous_handover_id=prev.id if prev else None, new_since_previous=since)
