"""Database tables (SQLModel). SQLite for development, PostgreSQL (Cloud SQL) via DATABASE_URL."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


class MapStatus(StrEnum):
    processing = "processing"
    ready = "ready"
    failed = "failed"


class Map(SQLModel, table=True):
    """The site map: a trained, metric Gaussian splat (+ its training views for the VPS)."""
    id: str = Field(primary_key=True)            # e.g. "property-a-3-04", used by the app as map_id
    name: str                                    # e.g. "Unit 3.04"
    property: str | None = None                  # e.g. "Property A"
    floor_label: str | None = None               # e.g. "3rd floor"
    scanned_at: datetime | None = None
    created_at: datetime = Field(default_factory=now)
    status: MapStatus = Field(default=MapStatus.processing)
    error: str | None = None
    up_axis: str = "z"
    north: list = Field(default_factory=lambda: [0.0, 1.0], sa_column=Column(JSON))   # floor-plane direction
    floor_z: float | None = None                 # auto from the splat if not given
    rooms: list = Field(default_factory=list, sa_column=Column(JSON))   # [{"name", "polygon": [[x,y],...]}]
    floor_plan_key: str | None = None
    floor_plan: dict = Field(default_factory=dict, sa_column=Column(JSON))   # pixel <-> floor meta
    context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    splat_key: str                               # 3DGS .ply, also what the viewer loads
    proj_key: str | None = None                  # opaque gaussian centers (+ indices) for lifting
    views_key: str | None = None                 # zip of training views for the VPS index
    views_to_map: list | None = Field(default=None, sa_column=Column(JSON))
    vps_status: str = "none"                     # none | building | ready | failed
    vps_info: dict = Field(default_factory=dict, sa_column=Column(JSON))
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Handover(SQLModel, table=True):
    """Move-in / move-out walkthrough of a unit; records made during it are linked."""
    id: str = Field(default_factory=new_id, primary_key=True)
    map_id: str = Field(foreign_key="map.id", index=True)
    kind: str = "move_in"                        # move_in | move_out
    tenant: str | None = None
    started_by: str | None = None
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
    status: str = "open"                         # open | finished
    notes: str | None = None


class CaptureStatus(StrEnum):
    awaiting_upload = "awaiting_upload"   # /v1 flow: upload URL issued
    uploaded = "uploaded"
    processing = "processing"
    done = "done"                         # fully processed (with or without damage)
    needs_localization = "needs_localization"   # no pose in the map yet (no VPS / VPS failed)
    needs_review = "needs_review"         # pose confidence or image quality below the gate
    rejected = "rejected"                 # image quality gate (if rejection is enabled)
    failed = "failed"


class Capture(SQLModel, table=True):
    """One inspection input: a photo (+ optional video frames) from a phone, glasses or robot."""
    id: str = Field(default_factory=new_id, primary_key=True)
    map_id: str | None = Field(default=None, foreign_key="map.id", index=True)
    status: CaptureStatus = Field(default=CaptureStatus.processing, index=True)
    error: str | None = None
    # pipeline step reached: 0 upload, 1 localize, 2 detect (SAM 3), 3 place in 3D, 4 match, 5 done
    stage: int = 0
    size_bytes: int | None = None                # upload size (zip or photo)
    created_at: datetime = Field(default_factory=now, index=True)
    updated_at: datetime = Field(default_factory=now)

    source_kind: str = "phone"                   # phone | glasses | robot | fixed
    device_id: str | None = None
    recorder: str | None = None                  # person who recorded, e.g. "M. Keller"
    event_kind: str | None = None                # move_in | move_out | repair | tenant_report | inspection
    handover_id: str | None = Field(default=None, foreign_key="handover.id", index=True)
    original_sha256: str | None = None           # of the photo / zip exactly as uploaded
    device_model: str | None = None
    app_version: str | None = None
    captured_at: datetime | None = None
    geo: dict = Field(default_factory=dict, sa_column=Column(JSON))

    package_key: str | None = None               # uploaded zip (/v1 flow)
    image_key: str | None = None                 # upright photo
    image_width: int | None = None
    image_height: int | None = None
    photo_camera: dict = Field(default_factory=dict, sa_column=Column(JSON))   # device-tracking pose (c2w_opencv) + K
    n_frames: int = 0
    # frames kept for localization: [{name, image_key, K, c2w (device, OpenCV), t}]
    frames: list = Field(default_factory=list, sa_column=Column(JSON))
    quality: dict = Field(default_factory=dict, sa_column=Column(JSON))
    localization: dict = Field(default_factory=dict, sa_column=Column(JSON))   # method, confidence, camera in map

    has_damage: bool | None = Field(default=None, index=True)
    overlay_key: str | None = None
    detector_model: str | None = None
    detection_latency_s: float | None = None


class Detection(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    capture_id: str = Field(foreign_key="capture.id", index=True)
    damage_class: str = Field(index=True)
    prompt: str
    confidence: float
    bbox: list = Field(default_factory=list, sa_column=Column(JSON))        # [x0, y0, x1, y1] px
    polygons: list = Field(default_factory=list, sa_column=Column(JSON))    # [[[x, y], ...], ...] px
    area_px: float = 0.0
    area_ratio: float = 0.0
    covers_full_frame: bool = False
    mask_key: str | None = None


class IssueStatus(StrEnum):
    new = "new"
    worsening = "worsening"
    stable = "stable"
    repaired = "repaired"     # confirmed by a human
    rejected = "rejected"     # all observations rejected in review


OPEN_STATUSES = (IssueStatus.new, IssueStatus.worsening, IssueStatus.stable)


class Issue(SQLModel, table=True):
    seq: int | None = Field(default=None, primary_key=True)       # ISS-0001
    map_id: str = Field(foreign_key="map.id", index=True)
    damage_class: str = Field(index=True)
    status: IssueStatus = Field(default=IssueStatus.new, index=True)
    severity: str = "medium"
    severity_manual: bool = False
    object_id: str | None = None
    object_label: str | None = None
    position: list = Field(default_factory=list, sa_column=Column(JSON))   # m, map frame
    normal: list = Field(default_factory=list, sa_column=Column(JSON))
    room: str | None = None
    surface: str | None = None                   # floor | ceiling | north wall | ...
    height_m: float | None = None                # above floor
    floor_xy: list = Field(default_factory=list, sa_column=Column(JSON))
    type_manual: bool = False                    # type corrected by a person
    size: float | None = None                    # last length (m) or area (m²), see classes.size_metric
    first_seen: datetime = Field(default_factory=now)
    last_seen: datetime = Field(default_factory=now)
    closed_at: datetime | None = None            # set when repaired / rejected
    closed_by: str | None = None                 # who confirmed the repair
    repair_suggested: bool = False              # a confident view of the spot showed no damage
    repair_suggested_at: datetime | None = None
    repair_suggested_capture_id: str | None = None

    @property
    def issue_id(self) -> str:
        return f"ISS-{self.seq:04d}"


class Observation(SQLModel, table=True):
    seq: int | None = Field(default=None, primary_key=True)       # OBS-0001
    issue_seq: int = Field(foreign_key="issue.seq", index=True)
    capture_id: str = Field(foreign_key="capture.id", index=True)
    detection_id: str = Field(foreign_key="detection.id")
    damage_class: str = ""                       # as detected (the issue's type may be corrected later)
    timestamp: datetime = Field(default_factory=now)
    source_kind: str = "phone"
    device_id: str | None = None
    recorder: str | None = None
    event_kind: str | None = None
    handover_id: str | None = None
    recorded_at: datetime = Field(default_factory=now)    # server time
    image_sha256: str | None = None
    prev_hash: str | None = None
    record_hash: str | None = None
    camera_t: list = Field(default_factory=list, sa_column=Column(JSON))
    camera_q: list = Field(default_factory=list, sa_column=Column(JSON))   # [x, y, z, w]
    pose_confidence: float | None = None
    image_key: str | None = None
    mask_key: str | None = None
    overlay_key: str | None = None
    length_m: float | None = None
    area_m2: float | None = None
    detector_confidence: float = 0.0
    position: list = Field(default_factory=list, sa_column=Column(JSON))
    normal: list = Field(default_factory=list, sa_column=Column(JSON))
    n_points: int = 0
    points_key: str | None = None                # [x,y,z] list (max 5000)
    indices_key: str | None = None               # gaussian indices into the map splat
    change: str = "new"                          # new | worsening | stable
    review: str = "unreviewed"                   # unreviewed | confirmed | rejected | superseded
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    @property
    def obs_id(self) -> str:
        return f"OBS-{self.seq:04d}"
