"""Database tables (SQLModel). SQLite for development, any SQLAlchemy URL (Postgres) later."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def now() -> datetime:
    return datetime.now(UTC)


class CaptureStatus(StrEnum):
    received = "received"
    detecting = "detecting"
    no_damage = "no_damage"
    damage_detected = "damage_detected"      # damage found, no 3D location yet
    registering = "registering"              # aligning the ARKit capture with the building scan
    needs_alignment = "needs_alignment"      # automatic alignment failed / too uncertain
    reconstructing = "reconstructing"        # splat job claimed by a worker
    localizing = "localizing"
    completed = "completed"                  # 3D damage annotations available
    failed = "failed"


class SceneStatus(StrEnum):
    processing = "processing"
    ready = "ready"
    failed = "failed"


class Scene(SQLModel, table=True):
    """Pre-scanned building / apartment (LiDAR scanner point cloud, metric)."""
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=now)
    status: SceneStatus = Field(default=SceneStatus.processing)
    error: str | None = None
    up_axis: str = "z"
    units: str = "m"
    context: dict = Field(default_factory=dict, sa_column=Column(JSON))
    cloud_key: str                       # original upload
    preview_key: str | None = None       # downsampled .ply for the frontend
    proj_key: str | None = None          # downsampled .npy used for projection / registration
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))


class JobStatus(StrEnum):
    pending = "pending"
    claimed = "claimed"
    completed = "completed"
    failed = "failed"


class Capture(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    created_at: datetime = Field(default_factory=now, index=True)
    updated_at: datetime = Field(default_factory=now)
    status: CaptureStatus = Field(default=CaptureStatus.received, index=True)
    error: str | None = None

    # source: "phone" today, "drone" later
    source_type: str = "phone"
    device_model: str | None = None
    device_os: str | None = None
    app_version: str | None = None
    captured_at: datetime | None = None

    # location (WGS84) + optional free-form context (building, floor, room, notes, ...)
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    gps_accuracy_m: float | None = None
    heading_deg: float | None = None
    context: dict = Field(default_factory=dict, sa_column=Column(JSON))

    image_key: str
    image_content_type: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    image_sha256: str | None = None
    # ARKit pose + intrinsics of the photo (localization.Camera dict, convention "arkit")
    photo_camera: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # per-video-frame ARKit poses (JSON file from the app, passed through to the splat team)
    arkit_frames_key: str | None = None
    arkit_session_id: str | None = Field(default=None, index=True)

    # building scan this capture belongs to + alignment ARKit world -> scene
    scene_id: str | None = Field(default=None, foreign_key="scene.id", index=True)
    scene_hint: dict = Field(default_factory=dict, sa_column=Column(JSON))   # {"center": [x,y,z], "radius": r}
    geometry_key: str | None = None      # ARKit LiDAR point cloud (.ply, ARKit world frame)
    registration: dict = Field(default_factory=dict, sa_column=Column(JSON))

    video_key: str | None = None
    video_content_type: str | None = None
    video_size_bytes: int | None = None
    video_duration_s: float | None = None
    video_width: int | None = None
    video_height: int | None = None
    video_fps: float | None = None

    has_damage: bool | None = Field(default=None, index=True)
    overlay_key: str | None = None
    detector_model: str | None = None
    detection_latency_s: float | None = None
    detected_at: datetime | None = None


class Detection(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    capture_id: str = Field(foreign_key="capture.id", index=True)
    damage_class: str = Field(index=True)
    prompt: str
    confidence: float
    # pixel coordinates in the original image
    bbox: list = Field(default_factory=list, sa_column=Column(JSON))        # [x0, y0, x1, y1]
    polygons: list = Field(default_factory=list, sa_column=Column(JSON))    # [[[x, y], ...], ...]
    area_px: float = 0.0
    area_ratio: float = 0.0          # mask area / image area
    covers_full_frame: bool = False  # SAM 3 sometimes masks the whole image on texture close-ups


class ReconstructionJob(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    capture_id: str = Field(foreign_key="capture.id", index=True)
    status: JobStatus = Field(default=JobStatus.pending, index=True)
    created_at: datetime = Field(default_factory=now)
    claimed_at: datetime | None = None
    claimed_by: str | None = None
    completed_at: datetime | None = None
    error: str | None = None

    splat_key: str | None = None
    splat_format: str | None = None
    # pose + intrinsics of the damage photo inside the reconstruction (see localization.Camera)
    camera: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # free-form info from the splat team (units, scale, software, training time, ...)
    scene_info: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Damage3D(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    capture_id: str = Field(foreign_key="capture.id", index=True)
    detection_id: str = Field(foreign_key="detection.id", index=True)
    job_id: str | None = Field(default=None, foreign_key="reconstructionjob.id")
    scene_id: str | None = Field(default=None, foreign_key="scene.id", index=True)
    frame: str = "scene"            # "scene" (building scan, meters) or "splat" (per-capture splat)
    damage_class: str
    confidence: float
    # all in the splat's world coordinates / units
    centroid: list = Field(default_factory=list, sa_column=Column(JSON))
    bbox_min: list = Field(default_factory=list, sa_column=Column(JSON))
    bbox_max: list = Field(default_factory=list, sa_column=Column(JSON))
    normal: list = Field(default_factory=list, sa_column=Column(JSON))
    n_points: int = 0
    mean_depth: float | None = None
    # mask area projected at mean depth, ignoring surface tilt (m² if the scene is metric/ARKit)
    area_est: float | None = None
    indices_key: str | None = None   # splat only: JSON list of gaussian indices inside the damage
    points_key: str | None = None    # JSON list of [x, y, z] inside the damage (max 5000, subsampled)
    method: str = "mask_projection"
