"""Data for the web-viewer prototype (branch web-viewer, viewer/index.html), in the shape of its
MockApi, so the viewer can switch from mock data to the backend by replacing two calls:

    MockApi.listSites()   ->  GET /viewer/sites
    MockApi.getSite(id)   ->  GET /viewer/sites/{id}

Coordinates are converted to the viewer's three.js frame (y up, cameras looking along -z).
The splat itself stays in the map frame: apply `splat_to_viewer` (4x4) to the splat mesh.
The procedural mock geometry (segments, plan, elevation) is replaced by `splat_url` and
`floor_plan_url`.
"""

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from inspection.classes import DAMAGE_CLASSES
from localization.cameras import c2w_to_tq
from localization.vps import MULTI_FRAME_INLIERS_FULL

from .config import settings
from .db import get_session
from .models import Capture, CaptureStatus, Issue, IssueStatus, Map, Observation, now
from .schemas import live_observations, place_of
from .storage import storage

router = APIRouter(prefix="/viewer", tags=["viewer"])

# backend type -> key of the viewer's TYPES table (others are sent as-is, see `types`)
VIEWER_TYPE = {"stain": "leak", "coating_damage": "coating"}
GL_FROM_CV = np.diag([1.0, -1.0, -1.0])


def _to_viewer_matrix(up_axis: str) -> np.ndarray:
    """Map frame -> viewer frame (y up)."""
    if up_axis == "y":
        return np.eye(3)
    return np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])      # z-up: (x, y, z) -> (x, z, -y)


def _v(M, p) -> list[float]:
    return [round(float(x), 4) for x in M @ np.asarray(p, dtype=np.float64)]


def _pose(M, t, q) -> dict:
    """camera_pose of the backend (OpenCV camera axes, map frame) -> viewer (three.js camera, y up)."""
    x, y, z, w = q
    R = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    c2w = np.eye(4)
    c2w[:3, :3], c2w[:3, 3] = M @ R @ GL_FROM_CV, M @ np.asarray(t, dtype=np.float64)
    tv, qv = c2w_to_tq(c2w)
    return {"t": tv, "q": qv}


def _pose_c2w(M, c2w) -> dict:
    """4x4 camera->world (OpenCV axes, map frame) -> viewer pose."""
    c2w = np.asarray(c2w, dtype=np.float64)
    out = np.eye(4)
    out[:3, :3], out[:3, 3] = M @ c2w[:3, :3] @ GL_FROM_CV, M @ c2w[:3, 3]
    tv, qv = c2w_to_tq(out)
    return {"t": tv, "q": qv}


def _vps_entry(M, c: Capture) -> dict | None:
    """Localization of a capture for the viewer's VPS layer (also without damage): the photo's pose,
    the video frames placed with the device->map alignment, and each frame's own VPS pose."""
    loc = c.localization or {}
    if not loc:
        return None
    cam = loc.get("camera")
    al = loc.get("alignment") or {}
    A = np.asarray(al["matrix"], dtype=np.float64) if al.get("matrix") else None
    vps_frames = {f["name"]: (k, f) for k, f in enumerate(loc.get("frames") or [])}
    inl = set(al.get("inlier_frames") or [])
    failed = set(loc.get("frames_failed") or [])
    frames = []
    for f in c.frames or []:
        k, v = vps_frames.get(f["name"], (None, None))
        frames.append({
            "name": f["name"], "t": f.get("t"),
            "aligned_pose": _pose_c2w(M, A @ np.asarray(f["c2w"])) if A is not None and f.get("c2w") else None,
            "vps_pose": _pose_c2w(M, v["c2w"]) if v and v.get("c2w") else None,
            "inliers": v["num_inliers"] if v else None,
            "localized": True if v else (False if f["name"] in failed else None),
            "consistent": (k in inl) if k is not None and al else None,
        })
    w, h, fy = (cam or {}).get("width"), (cam or {}).get("height"), (cam or {}).get("fy")
    return {
        "method": loc.get("method"), "confidence": loc.get("confidence"), "threshold": settings.min_pose_confidence,
        "error": loc.get("error"), "room": loc.get("room"),
        "camera_pose": _pose_c2w(M, cam["matrix"]) if cam else None,
        "fov_y": round(float(2 * np.degrees(np.arctan(h / 2 / fy))), 2) if w and h and fy else None,
        "aspect": round(w / h, 4) if w and h else None,
        "num_inliers": loc.get("num_inliers"),
        "inliers_full": MULTI_FRAME_INLIERS_FULL if loc.get("method") == "vps_frames" else None,
        "alignment": {k: al.get(k) for k in ("n_frames", "n_inliers", "max_dev_m", "max_dev_deg", "confidence")} if al else None,
        "frames": frames, "frames_failed": loc.get("frames_failed") or [],
    }


def _types() -> dict:
    out = {}
    for k, c in DAMAGE_CLASSES.items():
        out[VIEWER_TYPE.get(k, k)] = {"label": c["label"], "short": c["label"].split(" /")[0],
                                      "prompt": (c["prompts"] or [""])[0], "metric": c["size_metric"]}
    return out


def _capture_entry(s: Session, c: Capture, M=None) -> dict:
    status = {
        CaptureStatus.awaiting_upload: "processing", CaptureStatus.uploaded: "uploaded",
        CaptureStatus.processing: "processing", CaptureStatus.done: "done",
    }.get(c.status, "failed")
    obs = s.exec(select(Observation).where(Observation.capture_id == c.id,
                                           Observation.review != "superseded")).first()
    entry = {"capture_id": c.id, "created": (c.captured_at or c.created_at).isoformat(),
             "source": c.source_kind, "status": status, "stage": c.stage, "frames": c.n_frames,
             "size_mb": round(c.size_bytes / 1e6, 1) if c.size_bytes else None,
             "pose_confidence": (c.localization or {}).get("confidence"),
             "obs_id": obs.obs_id if obs else None, "issue_id": f"ISS-{obs.issue_seq:04d}" if obs else None,
             "vps": _vps_entry(M, c) if M is not None else None}
    if status == "failed":
        entry["reason"] = c.error or c.status
    elif status == "done":
        n = len(s.exec(select(Observation).where(Observation.capture_id == c.id,
                                                 Observation.review != "superseded")).all())
        entry["result"] = "No damage found" if not c.has_damage else (
            f"{n} finding{'s' if n != 1 else ''} placed" if n else "Damage found, not placed in 3D")
    return entry


@router.get("/sites")
def list_sites(s: Session = Depends(get_session)):
    """MockApi.listSites(): [{id, option}]"""
    out = []
    for m in s.exec(select(Map).order_by(Map.created_at.desc())).all():
        cat = (m.context or {}).get("category", "Unit")
        out.append({"id": m.id, "option": f"{m.name} · {cat}"})
    return out


@router.get("/sites/{map_id}")
def get_site(map_id: str, s: Session = Depends(get_session)):
    """MockApi.getSite(id): site with summary, monthly counts, issues (README record) and captures."""
    from .main import dashboard          # local import: main includes this router

    m = s.get(Map, map_id)
    if m is None:
        raise HTTPException(404, "site not found")
    M = _to_viewer_matrix(m.up_axis)
    ctx = m.context or {}
    dash = dashboard(map_id, s)

    lo = np.asarray(m.stats.get("bounds_min", [0, 0, 0]))
    hi = np.asarray(m.stats.get("bounds_max", [1, 1, 1]))
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) @ M.T
    bmin, bmax = corners.min(axis=0), corners.max(axis=0)
    floor_y = float((M @ np.array([0, 0, m.floor_z or 0.0]))[1]) if m.up_axis == "z" else (m.floor_z or 0.0)
    center = [round(float((bmin[0] + bmax[0]) / 2), 3), round(floor_y + 0.5, 3), round(float((bmin[2] + bmax[2]) / 2), 3)]

    issues = []
    for i in s.exec(select(Issue).where(Issue.map_id == map_id, Issue.status != IssueStatus.rejected)
                    .order_by(Issue.seq)).all():
        obs = live_observations(s, i.seq)
        place = place_of(i)
        issues.append({
            "issue_id": i.issue_id, "type": VIEWER_TYPE.get(i.damage_class, i.damage_class), "severity": i.severity,
            "status": i.status, "surface": i.surface, "where": i.room,
            "object": {"id": i.object_id or f"{i.room or 'unit'}-{i.surface or 'surface'}".replace(" ", "-").lower(),
                       "label": i.object_label or i.surface or place, "short": i.object_label or i.surface or place},
            "position_m": _v(M, i.position), "normal": _v(M, i.normal) if i.normal else None,
            "height_m": i.height_m, "floor_xy": i.floor_xy,
            "repaired_at": i.closed_at.isoformat() if i.status == IssueStatus.repaired and i.closed_at else None,
            "repaired_by": i.closed_by if i.status == IssueStatus.repaired else None,
            "repair_suggested": i.repair_suggested,
            "observations": [{
                "obs_id": o.obs_id, "timestamp": o.timestamp.isoformat(),
                "source": {"kind": o.source_kind, "device_id": o.device_id},
                "camera_pose": _pose(M, o.camera_t, o.camera_q) if o.camera_t else None,
                "pose_confidence": o.pose_confidence,
                "image_url": storage.url(o.image_key) if o.image_key else None,
                "mask_url": storage.url(o.mask_key) if o.mask_key else None,
                "overlay_url": storage.url(o.overlay_key) if o.overlay_key else None,
                "length_m": o.length_m, "area_m2": o.area_m2, "detector_confidence": o.detector_confidence,
                "reviewed_by": o.reviewed_by, "capture_id": o.capture_id,
                "recorder": o.recorder, "event_kind": o.event_kind, "record_hash": o.record_hash,
            } for o in obs],
        })

    caps = s.exec(select(Capture).where(Capture.map_id == map_id).order_by(Capture.created_at.desc())).all()
    splat_to_viewer = np.eye(4)
    splat_to_viewer[:3, :3] = M
    return {
        "id": m.id, "map_id": m.id, "name": m.name, "option": f"{m.name} · {ctx.get('category', 'Unit')}",
        "kind": ctx.get("kind", "interior"), "category": ctx.get("category", "Unit"),
        "location": ", ".join(x for x in (m.property, m.floor_label) if x) or ctx.get("location"),
        "scanned_at": (m.scanned_at or m.created_at).isoformat(),      # viewer shows 1970 for null
        "status": m.status, "vps_status": m.vps_status,
        "splat_url": storage.url(m.splat_key), "splat_to_viewer": splat_to_viewer.tolist(),
        "viewer_splat_url": storage.url(m.viewer_splat_key) if m.viewer_splat_key else None,
        "floor_plan_url": storage.url(m.floor_plan_key) if m.floor_plan_key else None, "floor_plan": m.floor_plan,
        "rooms": m.rooms, "gaussians": m.stats.get("n_gaussians"),
        "camera": {"dir": [0.0, 0.707, 0.707], "center": center,
                   "box": [[round(float(v), 3) for v in bmin], [round(float(v), 3) for v in bmax]]},
        "summary": {"repaired_this_year": dash.closed_this_year, "avg_days_to_repair": dash.days_to_repair_avg,
                    "observations_total": dash.records, "open_findings": dash.open_findings},
        "monthly": [x["count"] for x in dash.records_per_month],
        "types": _types(),
        "issues": issues,
        "captures": [_capture_entry(s, c, M) for c in caps],
        "generated_at": now().isoformat(),
    }

