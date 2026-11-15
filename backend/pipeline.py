"""The inspection pipeline (README "Inspect & log damage"), shared by all inputs:

    app zip (/v1)  ─┐
    POST /inspect  ─┼─► quality gate ─► detect (SAM 3) ─► localize ─► lift onto splat ─► match issues
    re-localize    ─┘                                     │
                                                          ├─ pose from the client (robot / glasses)
                                                          ├─ VPS on video frames + ARKit poses → photo
                                                          └─ VPS on the photo itself
"""

import io
import json
import logging
import shutil
import zipfile

import numpy as np
from PIL import Image
from sqlmodel import Session, select

from inspection import evidence, floorplan, quality, render, splatfile
from inspection.classes import enabled_classes
from inspection.detection import get_detector
from inspection.issues import OpenIssue, change, find_match, size_of
from inspection.lifting import Camera, check_aspect, is_visible, lift, read_ply_points, visible_points
from inspection.placement import place, room_at, to_zup
from inspection.severity import max_severity, severity
from localization.alignment import alignment_from_frame_poses
from localization.cameras import Frame, Intrinsics, c2w_to_tq
from localization.frames import select_frames
from localization.vps import get_vps, multi_frame_confidence

from .config import settings
from .db import engine
from .models import (OPEN_STATUSES, Capture, CaptureStatus, Detection, Issue, IssueStatus, Map, MapStatus,
                     Observation, aware, now)
from .package import read_package
from .storage import storage

log = logging.getLogger(__name__)
MAX_POINTS_OUT = 5000
MIN_OPACITY = 0.2
REPAIR_MAX_DIST_M = 4.0


def _set(s: Session, cap: Capture, status: CaptureStatus, error: str | None = None) -> None:
    cap.status, cap.error, cap.updated_at = status, error, now()
    s.add(cap)
    s.commit()


# ------------------------------------------------------------------ entry points

def process_package(capture_id: str) -> None:
    """/v1 flow: the app's zip is in storage; unpack it and inspect."""
    with Session(engine) as s:
        cap = s.get(Capture, capture_id)
        _set(s, cap, CaptureStatus.processing)
        try:
            pkg = read_package(storage.local_path(cap.package_key), select_frames=select_frames)
        except Exception as e:
            log.exception("bad capture package %s", capture_id)
            _set(s, cap, CaptureStatus.failed, f"package: {e}")
            return
        m = pkg.manifest
        cap.device_model = m.get("device")
        cap.app_version = m.get("app_version")
        cap.captured_at = pkg.captured_at
        cap.geo = m.get("geo") or {}
        cap.n_frames = len((m.get("video") or {}).get("frames") or [])
        pkg_bytes = storage.local_path(cap.package_key).read_bytes()
        cap.original_sha256, cap.size_bytes = evidence.sha256(pkg_bytes), len(pkg_bytes)
        s.add(cap)
        s.commit()
    inspect(capture_id, pkg.photo, pkg.frames)


def inspect(capture_id: str, photo: Frame, frames: list[Frame] | None = None,
            map_pose: np.ndarray | None = None, map_pose_confidence: float | None = None) -> None:
    """Full pipeline for one capture. `map_pose`: camera->world (OpenCV) in the map, if the client
    is already localized (robot, glasses); otherwise the VPS is used."""
    frames = frames or []
    with Session(engine) as s:
        cap = s.get(Capture, capture_id)
        _set(s, cap, CaptureStatus.processing)
        try:
            # photo (upright) + its device-tracking pose
            cap.image_key = f"captures/{cap.id}/photo.jpg"
            storage.put_bytes(cap.image_key, photo.image, "image/jpeg")
            cap.image_width, cap.image_height = photo.K.width, photo.K.height
            cap.photo_camera = photo.camera_dict() if photo.c2w is not None else {"K": photo.K.to_dict()}
            cap.frames = []
            for f in frames:
                key = f"captures/{cap.id}/frames/{f.name}.jpg"
                storage.put_bytes(key, f.image, "image/jpeg")
                cap.frames.append({"name": f.name, "image_key": key, "K": f.K.to_dict(), "t": f.t,
                                   "c2w": None if f.c2w is None else np.asarray(f.c2w).round(6).tolist()})

            # 1. quality gate
            cap.quality = quality.assess(photo.image)
            if not cap.quality["ok"] and settings.reject_low_quality_images:
                _set(s, cap, CaptureStatus.rejected, "image quality: " + ", ".join(cap.quality["flags"]))
                return

            # 2. localize (VPS / client pose)
            cap.stage = 1
            s.add(cap)
            s.commit()
            m = s.get(Map, cap.map_id) if cap.map_id else None
            cap.localization = _localize(m, photo, frames, map_pose, map_pose_confidence)
            cap.stage = 2
            s.add(cap)
            s.commit()

            # 3. detect (SAM 3) - also when localization failed, the 2D findings are kept
            _detect(s, cap, photo)
            _finish(s, cap, m)
        except Exception as e:
            log.exception("inspection failed for %s", capture_id)
            s.rollback()
            cap = s.get(Capture, capture_id)
            _set(s, cap, CaptureStatus.failed, str(e))


def relocalize(capture_id: str, loc: dict) -> None:
    """Apply a localization given later (external VPS result, manual alignment) and finish."""
    with Session(engine) as s:
        cap = s.get(Capture, capture_id)
        m = s.get(Map, cap.map_id)
        cap.localization = loc
        s.add(cap)
        s.commit()
        try:
            _finish(s, cap, m)
        except Exception as e:
            log.exception("relocalization failed for %s", capture_id)
            s.rollback()
            _set(s, s.get(Capture, capture_id), CaptureStatus.failed, str(e))


# ------------------------------------------------------------------ steps

def _detect(s: Session, cap: Capture, photo: Frame) -> None:
    result = get_detector().detect(photo.image, photo.K.width, photo.K.height)
    for old in s.exec(select(Detection).where(Detection.capture_id == cap.id)).all():
        s.delete(old)
    for d in result.detections:
        det = Detection(capture_id=cap.id, damage_class=d.damage_class, prompt=d.prompt,
                        confidence=d.confidence, bbox=d.bbox, polygons=d.polygons, area_px=d.area_px,
                        area_ratio=d.area_ratio, covers_full_frame=d.covers_full_frame)
        det.mask_key = f"captures/{cap.id}/mask_{det.id}.png"
        storage.put_bytes(det.mask_key, render.mask_png(d.polygons, photo.K.width, photo.K.height), "image/png")
        s.add(det)
    storage.put_bytes(f"captures/{cap.id}/detection_raw.json", json.dumps(result.raw).encode(), "application/json")
    if result.detections:
        cap.overlay_key = f"captures/{cap.id}/overlay.jpg"
        storage.put_bytes(cap.overlay_key, render.overlay(photo.image, result.detections), "image/jpeg")
    cap.has_damage = bool(result.detections)
    cap.detector_model, cap.detection_latency_s = result.model, result.latency_s
    s.add(cap)
    s.commit()


def _localize(m: Map | None, photo: Frame, frames: list[Frame], map_pose, map_pose_confidence) -> dict:
    """Return {"method", "confidence", "camera" (photo in the map, c2w_opencv dict) | None, ...}."""
    if m is None:
        return {"method": None, "confidence": None, "camera": None, "error": "capture has no map_id"}
    if map_pose is not None:
        return {"method": "client", "confidence": 1.0 if map_pose_confidence is None else map_pose_confidence,
                "camera": photo.camera_dict(np.asarray(map_pose))}
    vps = get_vps()
    if vps is None or not vps.has_index(m.id):
        return {"method": None, "confidence": None, "camera": None,
                "error": "no VPS index for this map" if vps else "no VPS configured"}

    # multi-frame: localize video frames, carry the result to the photo via the ARKit poses
    if frames and photo.c2w is not None:
        res = vps.localize(m.id, frames)
        ok = [(f, r) for f, r in zip(frames, res) if r is not None]
        if ok:
            al = alignment_from_frame_poses([f.c2w for f, _ in ok], [r.c2w for _, r in ok])
            conf = multi_frame_confidence(al["confidence"], [r.num_inliers for _, r in ok])
            T = np.asarray(al["matrix"])
            return {"method": "vps_frames", "confidence": round(conf, 3),
                    "camera": photo.camera_dict(T @ photo.c2w), "alignment": al,
                    "frames": [{"name": f.name, "num_inliers": r.num_inliers} for f, r in ok]}
    # single image
    r = vps.localize(m.id, [photo])[0]
    if r is None:
        return {"method": "vps_photo", "confidence": 0.0, "camera": None, "error": "VPS could not localize"}
    return {"method": "vps_photo", "confidence": r.confidence, "camera": photo.camera_dict(r.c2w),
            "num_inliers": r.num_inliers}


def _finish(s: Session, cap: Capture, m: Map | None) -> None:
    loc = cap.localization or {}
    if m is None or not loc.get("camera"):
        cap.stage = 1 if m else 5
        _set(s, cap, CaptureStatus.needs_localization if m else CaptureStatus.done, loc.get("error"))
        return
    if m.status != MapStatus.ready:
        cap.stage = 1
        _set(s, cap, CaptureStatus.needs_localization, f"map {m.id} is {m.status}")
        return
    cap.localization = {**loc, "room": camera_room(m, loc["camera"])}
    loc = cap.localization
    s.add(cap)
    s.commit()
    if (loc.get("confidence") or 0) < settings.min_pose_confidence:
        cap.stage = 1
        _set(s, cap, CaptureStatus.needs_review,
             f"Pose confidence {loc.get('confidence')} is below the {settings.min_pose_confidence:.2f} threshold")
        return
    if not cap.quality.get("ok", True):
        cap.stage = 2
        _set(s, cap, CaptureStatus.needs_review, "image quality: " + ", ".join(cap.quality.get("flags", [])))
        return
    cap.stage = 3
    s.add(cap)
    s.commit()
    _lift_and_match(s, cap, m, loc)
    cap.stage = 5
    _set(s, cap, CaptureStatus.done)


def camera_room(m: Map, camera: dict) -> str | None:
    c = to_zup(np.asarray(camera["matrix"])[:3, 3], m.up_axis)
    r = room_at(c[:2], m.rooms)
    return r["name"] if r else None


def hash_fields(o: Observation) -> dict:
    """The content an observation's record hash covers."""
    return {"obs": o.seq, "issue": o.issue_seq, "capture": o.capture_id, "type": o.damage_class,
            "timestamp": aware(o.timestamp).isoformat() if o.timestamp else None,
            "recorded_at": aware(o.recorded_at).isoformat() if o.recorded_at else None,
            "position": o.position, "length_m": o.length_m, "area_m2": o.area_m2,
            "image_sha256": o.image_sha256, "recorder": o.recorder, "event": o.event_kind,
            "device": o.device_id}


def _store_points(key: str, pts: np.ndarray) -> None:
    if len(pts) > MAX_POINTS_OUT:
        pts = pts[np.random.default_rng(0).choice(len(pts), MAX_POINTS_OUT, replace=False)]
    storage.put_bytes(key, json.dumps(np.round(pts, 4).tolist()).encode(), "application/json")


def _lift_and_match(s: Session, cap: Capture, m: Map, loc: dict) -> None:
    proj = np.load(storage.local_path(m.proj_key))
    xyz, gidx = proj["xyz"].astype(np.float64), proj["idx"]
    cam = Camera.from_dict(loc["camera"])
    check_aspect(cam, (cap.image_width, cap.image_height))
    vis = visible_points(xyz, cam)
    t, q = c2w_to_tq(np.asarray(loc["camera"]["matrix"]))
    ts = aware(cap.captured_at or cap.created_at)

    # re-processing a capture supersedes its earlier observations (kept: the hash chain stays intact)
    for o in s.exec(select(Observation).where(Observation.capture_id == cap.id,
                                              Observation.review != "superseded")).all():
        o.review = "superseded"
        s.add(o)
    s.flush()
    image_sha = evidence.sha256(storage.local_path(cap.image_key).read_bytes())
    last = s.exec(select(Observation).join(Issue, Observation.issue_seq == Issue.seq)
                  .where(Issue.map_id == m.id, Observation.record_hash.is_not(None))
                  .order_by(Observation.seq.desc())).first()
    prev_hash = last.record_hash if last else evidence.GENESIS
    place_kw = {"up_axis": m.up_axis, "north": m.north, "floor_z": m.floor_z, "rooms": m.rooms}

    open_issues = s.exec(select(Issue).where(Issue.map_id == m.id, Issue.status.in_(OPEN_STATUSES))).all()
    cands = [OpenIssue(i.seq, i.damage_class, i.position, i.object_id, i.bbox_min or None, i.bbox_max or None)
             for i in open_issues]
    matched: set[int] = set()

    for d in s.exec(select(Detection).where(Detection.capture_id == cap.id)
                    .order_by(Detection.confidence.desc())).all():
        lf = lift(d.polygons, (cap.image_width, cap.image_height), vis, cam, xyz)
        if lf is None:
            continue
        size = size_of(d.damage_class, lf.length_m, lf.area_m2)
        pl = place(lf.centroid, lf.normal, **place_kw)
        hit = find_match(d.damage_class, lf.centroid, None, cands, bbox_min=lf.bbox_min, bbox_max=lf.bbox_max)
        if hit is not None:
            issue = s.get(Issue, hit.key)
            ch = change(d.damage_class, issue.size, size)
            issue.status = IssueStatus.worsening if ch == "worsening" else IssueStatus.stable
            if not issue.severity_manual:
                issue.severity = max_severity(issue.severity,
                                              severity(d.damage_class, lf.length_m, lf.area_m2, issue.object_label))
            issue.size = size
            issue.bbox_min, issue.bbox_max = lf.bbox_min, lf.bbox_max
            for c in cands:
                if c.key == issue.seq:
                    c.bbox_min, c.bbox_max = lf.bbox_min, lf.bbox_max
            issue.last_seen = max(aware(issue.last_seen), ts)
            issue.repair_suggested = False
        else:
            ch = "new"
            issue = Issue(map_id=m.id, damage_class=d.damage_class, status=IssueStatus.new,
                          severity=severity(d.damage_class, lf.length_m, lf.area_m2),
                          position=lf.centroid, normal=lf.normal, size=size, first_seen=ts, last_seen=ts,
                          room=pl["room"], surface=pl["surface"], height_m=pl["height_m"], floor_xy=pl["floor_xy"],
                          bbox_min=lf.bbox_min, bbox_max=lf.bbox_max)
            s.add(issue)
            s.flush()
            cands.append(OpenIssue(issue.seq, issue.damage_class, issue.position, None, lf.bbox_min, lf.bbox_max))
        matched.add(issue.seq)
        s.add(issue)

        obs = Observation(issue_seq=issue.seq, capture_id=cap.id, detection_id=d.id, damage_class=d.damage_class,
                          timestamp=ts, source_kind=cap.source_kind, device_id=cap.device_id,
                          recorder=cap.recorder, event_kind=cap.event_kind, handover_id=cap.handover_id,
                          image_sha256=image_sha, camera_t=t, camera_q=q,
                          pose_confidence=loc.get("confidence"), image_key=cap.image_key, mask_key=d.mask_key,
                          overlay_key=cap.overlay_key, length_m=lf.length_m, area_m2=lf.area_m2,
                          detector_confidence=d.confidence, position=lf.centroid, normal=lf.normal,
                          n_points=lf.n_points, change=ch)
        s.add(obs)
        s.flush()
        obs.points_key = f"captures/{cap.id}/obs_{obs.seq}_points.json"
        _store_points(obs.points_key, xyz[lf.indices])
        obs.indices_key = f"captures/{cap.id}/obs_{obs.seq}_gaussians.json"
        storage.put_bytes(obs.indices_key, json.dumps(gidx[lf.indices].tolist()).encode(), "application/json")
        obs.prev_hash = prev_hash
        obs.record_hash = evidence.record_hash(hash_fields(obs), prev_hash)
        prev_hash = obs.record_hash
        s.add(obs)

    # "Gone?": a confident view of a known issue's spot without a matching finding
    detectable = set(enabled_classes())
    for issue in open_issues:
        if issue.seq in matched or issue.damage_class not in detectable:
            continue
        p = np.asarray(issue.position)
        to_cam = cam.center - p
        dist = float(np.linalg.norm(to_cam))
        facing = not issue.normal or np.dot(issue.normal, to_cam) > 0.3 * dist
        if dist <= REPAIR_MAX_DIST_M and facing and is_visible(p, vis, xyz):
            issue.repair_suggested = True
            issue.repair_suggested_at = now()
            issue.repair_suggested_capture_id = cap.id
            s.add(issue)
    s.commit()


# ------------------------------------------------------------------ maps

def process_map(map_id: str) -> None:
    """Extract opaque gaussian centers for lifting; build the VPS index if views were uploaded."""
    with Session(engine) as s:
        m = s.get(Map, map_id)
        try:
            xyz, opacity = read_ply_points(storage.local_path(m.splat_key))
            keep = np.ones(len(xyz), bool) if opacity is None else opacity >= MIN_OPACITY
            pts, idx = xyz[keep], np.flatnonzero(keep)
            m.proj_key = f"maps/{m.id}/proj.npz"
            buf = io.BytesIO()
            np.savez(buf, xyz=pts.astype(np.float32), idx=idx.astype(np.int64))
            storage.put_bytes(m.proj_key, buf.getvalue(), "application/octet-stream")
            lo, hi = np.percentile(pts, 1, axis=0), np.percentile(pts, 99, axis=0)
            m.stats = {"n_gaussians": int(len(xyz)), "n_used": int(len(pts)), "min_opacity": MIN_OPACITY,
                       "has_opacity": opacity is not None,
                       "bounds_min": np.round(lo, 3).tolist(), "bounds_max": np.round(hi, 3).tolist()}
            if m.floor_z is None:
                m.floor_z = round(float(np.percentile(to_zup(pts, m.up_axis)[:, 2], 2)), 3)
            try:
                tmp = settings.data_dir / "tmp" / f"{m.id}_viewer.ply"
                tmp.parent.mkdir(parents=True, exist_ok=True)
                m.stats = {**m.stats, "viewer_splat": splatfile.viewer_copy(storage.local_path(m.splat_key), tmp)}
                m.viewer_splat_key = f"maps/{m.id}/splat_viewer.ply"
                with open(tmp, "rb") as fh:
                    storage.put_file(m.viewer_splat_key, fh, "application/octet-stream")
                tmp.unlink()
            except ValueError as e:      # plain point cloud: the viewer uses the original
                log.info("no viewer splat for %s: %s", map_id, e)
            try:
                rgb = floorplan.read_ply_colors(storage.local_path(m.splat_key))
                png, meta = floorplan.render(pts, rgb[keep] if rgb is not None else None,
                                             up_axis=m.up_axis, floor_z=m.floor_z)
                m.floor_plan_key = f"maps/{m.id}/floor_plan.png"
                storage.put_bytes(m.floor_plan_key, png, "image/png")
                m.floor_plan = meta
            except Exception:
                log.exception("floor plan failed for %s", map_id)
            m.status, m.error = MapStatus.ready, None
        except Exception as e:
            log.exception("map processing failed for %s", map_id)
            m.status, m.error = MapStatus.failed, str(e)
        s.add(m)
        s.commit()
        build = m.status == MapStatus.ready and m.views_key and get_vps() is not None
    if build:
        build_vps_index(map_id)


def build_vps_index(map_id: str) -> None:
    vps = get_vps()
    with Session(engine) as s:
        m = s.get(Map, map_id)
        if vps is None or not m.views_key:
            m.vps_status, m.vps_info = "failed", {"error": "no VPS configured or no views uploaded"}
            s.add(m)
            s.commit()
            return
        m.vps_status = "building"
        s.add(m)
        s.commit()
        try:
            views_dir = settings.data_dir / "views" / m.id
            if views_dir.exists():
                shutil.rmtree(views_dir)
            with zipfile.ZipFile(storage.local_path(m.views_key)) as z:
                for name in z.namelist():
                    if name.startswith("/") or ".." in name.split("/"):
                        raise ValueError(f"unsafe path in views zip: {name}")
                z.extractall(views_dir)
            T = np.asarray(m.views_to_map) if m.views_to_map else None
            map_xyz = np.load(storage.local_path(m.proj_key))["xyz"].astype(np.float64)
            m.vps_info = vps.build_index(m.id, views_dir, views_to_map=T, map_xyz=map_xyz)
            m.vps_status = "ready"
        except Exception as e:
            log.exception("VPS index failed for %s", map_id)
            m.vps_status, m.vps_info = "failed", {"error": str(e)}
        s.add(m)
        s.commit()


def photo_from_capture(cap: Capture) -> Frame:
    """Rebuild the photo frame of a stored capture (for re-localization)."""
    img = storage.local_path(cap.image_key).read_bytes()
    pc = cap.photo_camera or {}
    if "matrix" in pc:
        K = Intrinsics.from_dict(pc)
        c2w = np.asarray(pc["matrix"])
    else:
        w, h = Image.open(io.BytesIO(img)).size
        K = Intrinsics.from_dict(pc.get("K") or {"fx": w, "fy": w, "cx": w / 2, "cy": h / 2, "width": w, "height": h})
        c2w = None
    return Frame(name="photo", image=img, K=K, c2w=c2w)

