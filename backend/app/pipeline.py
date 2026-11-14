"""Background steps.

  upload ─► run_detection ─► damage? ─┬─► run_scene_localization  (capture belongs to a building scan)
                                      │      align ARKit capture → scan, project masks → Damage3D(frame="scene")
                                      └─► ReconstructionJob for the splat team (if a video was sent)
                                             result ─► run_localization → Damage3D(frame="splat")

Runs as FastAPI background tasks (fine for a hackathon). For production, move these into a
queue (Cloud Tasks / Pub/Sub on GCP, Queues on Cloudflare) with the same functions as handlers.
"""

import json
import logging

import numpy as np
import requests
from sqlmodel import Session, select

from .config import settings
from .db import engine
from .detection import DAMAGE_CLASSES, get_detector
from .localization import Camera, check_aspect, localize, read_ply_points, visible_points
from .media import render_overlay
from .models import (Capture, CaptureStatus, Damage3D, Detection, JobStatus, ReconstructionJob,
                     Scene, SceneStatus, now)
from .storage import storage

log = logging.getLogger(__name__)
LABELS = {k: v["label"] for k, v in DAMAGE_CLASSES.items()}
MAX_POINTS_OUT = 5000


def _set_status(s: Session, cap: Capture, status: CaptureStatus, error: str | None = None) -> None:
    cap.status, cap.error, cap.updated_at = status, error, now()
    s.add(cap)
    s.commit()


# ------------------------------------------------------------------ detection

def run_detection(capture_id: str) -> None:
    with Session(engine) as s:
        cap = s.get(Capture, capture_id)
        if cap is None:
            return
        _set_status(s, cap, CaptureStatus.detecting)
        try:
            image_bytes = storage.local_path(cap.image_key).read_bytes()
            result = get_detector().detect(image_bytes, cap.image_width, cap.image_height)
        except Exception as e:
            log.exception("detection failed for %s", capture_id)
            _set_status(s, cap, CaptureStatus.failed, f"detection: {e}")
            return

        for old in s.exec(select(Detection).where(Detection.capture_id == capture_id)).all():
            s.delete(old)
        for d in result.detections:
            s.add(Detection(capture_id=capture_id, damage_class=d.damage_class, prompt=d.prompt,
                            confidence=d.confidence, bbox=d.bbox, polygons=d.polygons,
                            area_px=d.area_px, area_ratio=d.area_ratio,
                            covers_full_frame=d.covers_full_frame))

        storage.put_bytes(f"captures/{capture_id}/detection_raw.json", json.dumps(result.raw).encode())
        if result.detections:
            cap.overlay_key = f"captures/{capture_id}/overlay.jpg"
            storage.put_bytes(cap.overlay_key, render_overlay(image_bytes, result.detections, LABELS))

        cap.has_damage = bool(result.detections)
        cap.detector_model, cap.detection_latency_s, cap.detected_at = result.model, result.latency_s, now()
        cap.status = CaptureStatus.damage_detected if cap.has_damage else CaptureStatus.no_damage
        cap.updated_at = now()
        s.add(cap)

        job = None
        if cap.has_damage and cap.video_key:
            existing = s.exec(select(ReconstructionJob).where(
                ReconstructionJob.capture_id == capture_id,
                ReconstructionJob.status.in_([JobStatus.pending, JobStatus.claimed]))).first()
            if existing is None:
                job = ReconstructionJob(capture_id=capture_id)
                s.add(job)
        s.commit()
        has_damage, scene_id = cap.has_damage, cap.scene_id

    if job is not None and settings.splat_webhook_url:
        notify_webhook(job.id)
    if has_damage and scene_id:
        run_scene_localization(capture_id)


def notify_webhook(job_id: str) -> None:
    from .schemas import job_out  # local import to avoid a cycle
    with Session(engine) as s:
        payload = job_out(s, s.get(ReconstructionJob, job_id)).model_dump(mode="json")
    try:
        requests.post(settings.splat_webhook_url, json=payload, timeout=10)
    except Exception:
        log.warning("webhook to %s failed", settings.splat_webhook_url, exc_info=True)


# ------------------------------------------------------------------ building scan

def _session_registration(s: Session, cap: Capture) -> dict | None:
    """Reuse a good alignment from an earlier capture of the same ARKit session."""
    if not cap.arkit_session_id:
        return None
    others = s.exec(select(Capture).where(
        Capture.scene_id == cap.scene_id, Capture.arkit_session_id == cap.arkit_session_id,
        Capture.id != cap.id).order_by(Capture.created_at.desc())).all()
    for o in others:
        r = o.registration or {}
        if r.get("ok") and r.get("method") != "session":
            return {**r, "method": "session", "source_capture_id": o.id}
    return None


def _store_points(key: str, pts: np.ndarray) -> None:
    if len(pts) > MAX_POINTS_OUT:
        pts = pts[np.random.default_rng(0).choice(len(pts), MAX_POINTS_OUT, replace=False)]
    storage.put_bytes(key, json.dumps(np.round(pts, 4).tolist()).encode())


def run_scene_localization(capture_id: str, manual: dict | None = None) -> None:
    """Align the capture with its building scan (unless `manual` gives the alignment) and
    project every detection into the scan."""
    from .registration import from_correspondences, register, transform_camera  # heavy import

    with Session(engine) as s:
        cap = s.get(Capture, capture_id)
        scene = s.get(Scene, cap.scene_id) if cap and cap.scene_id else None
        if scene is None:
            return
        if scene.status != SceneStatus.ready:
            _set_status(s, cap, CaptureStatus.damage_detected, f"scene {scene.id} is {scene.status}")
            return
        if not cap.photo_camera:
            _set_status(s, cap, CaptureStatus.needs_alignment, "capture has no ARKit photo pose")
            return

        _set_status(s, cap, CaptureStatus.registering)
        try:
            scene_xyz = np.load(storage.local_path(scene.proj_key)).astype(np.float64)
            if manual and "matrix" in manual:
                T = np.asarray(manual["matrix"], dtype=np.float64)
                reg = {"matrix": T.round(6).tolist(), "method": "manual", "fitness": None,
                       "rmse": None, "ok": True}
            elif manual and "pairs" in manual:
                r = from_correspondences([p["arkit"] for p in manual["pairs"]],
                                         [p["scene"] for p in manual["pairs"]], scene.up_axis)
                reg = {**r.__dict__, "ok": r.rmse <= 0.05}
            elif (reused := _session_registration(s, cap)) is not None:
                reg = reused
            elif cap.geometry_key:
                cap_xyz, _ = read_ply_points(storage.local_path(cap.geometry_key))
                hint = cap.scene_hint or {}
                r = register(cap_xyz, scene_xyz, up_axis=scene.up_axis,
                             hint_center=hint.get("center"), hint_radius=hint.get("radius"))
                reg = {**r.__dict__, "ok": r.ok}
            else:
                _set_status(s, cap, CaptureStatus.needs_alignment,
                            "no ARKit geometry uploaded; send `geometry` or align manually")
                return
        except Exception as e:
            log.exception("registration failed for %s", capture_id)
            _set_status(s, cap, CaptureStatus.needs_alignment, f"registration: {e}")
            return

        T = np.asarray(reg["matrix"], dtype=np.float64)
        cam_scene = transform_camera(cap.photo_camera, T)
        cap.registration = {**reg, "registered_at": now().isoformat(), "photo_camera_scene": cam_scene}
        if not reg["ok"]:
            _set_status(s, cap, CaptureStatus.needs_alignment,
                        f"automatic alignment too uncertain (fitness {reg.get('fitness')}, rmse {reg.get('rmse')})")
            return

        _set_status(s, cap, CaptureStatus.localizing)
        try:
            cam = Camera.from_dict(cam_scene)
            vis = visible_points(scene_xyz, cam)
            for old in s.exec(select(Damage3D).where(Damage3D.capture_id == cap.id,
                                                     Damage3D.frame == "scene")).all():
                s.delete(old)
            n_ok = 0
            for d in s.exec(select(Detection).where(Detection.capture_id == cap.id)).all():
                loc = localize(d.polygons, (cap.image_width, cap.image_height), vis, cam, scene_xyz)
                if loc is None:
                    continue
                d3 = Damage3D(capture_id=cap.id, detection_id=d.id, scene_id=scene.id, frame="scene",
                              damage_class=d.damage_class, confidence=d.confidence,
                              centroid=loc.centroid, bbox_min=loc.bbox_min, bbox_max=loc.bbox_max,
                              normal=loc.normal, n_points=loc.n_points, mean_depth=loc.mean_depth,
                              area_est=loc.area_est)
                d3.points_key = f"captures/{cap.id}/damage3d_{d3.id}_points.json"
                _store_points(d3.points_key, scene_xyz[loc.indices])
                s.add(d3)
                n_ok += 1
            _set_status(s, cap, CaptureStatus.completed,
                        None if n_ok else "no detection could be projected into the scan")
        except Exception as e:
            log.exception("scene localization failed for %s", capture_id)
            _set_status(s, cap, CaptureStatus.failed, f"localization: {e}")


def run_scene_preprocessing(scene_id: str) -> None:
    from .registration import preprocess_scene
    with Session(engine) as s:
        scene = s.get(Scene, scene_id)
        try:
            out_dir = storage.local_path(scene.cloud_key).parent
            scene.stats = preprocess_scene(storage.local_path(scene.cloud_key), out_dir)
            scene.proj_key = f"scenes/{scene.id}/proj.npy"
            scene.preview_key = f"scenes/{scene.id}/preview.ply"
            scene.status, scene.error = SceneStatus.ready, None
        except Exception as e:
            log.exception("scene preprocessing failed for %s", scene_id)
            scene.status, scene.error = SceneStatus.failed, str(e)
        s.add(scene)
        s.commit()


# ------------------------------------------------------------------ per-capture splat

def run_localization(job_id: str) -> None:
    with Session(engine) as s:
        job = s.get(ReconstructionJob, job_id)
        cap = s.get(Capture, job.capture_id)
        scene_done = cap.status == CaptureStatus.completed
        if not scene_done:
            _set_status(s, cap, CaptureStatus.localizing)
        try:
            xyz, opacity = read_ply_points(storage.local_path(job.splat_key))
            cam = Camera.from_dict(job.camera)
            check_aspect(cam, (cap.image_width, cap.image_height))
            vis = visible_points(xyz, cam, opacity)
            for old in s.exec(select(Damage3D).where(Damage3D.capture_id == cap.id,
                                                     Damage3D.frame == "splat")).all():
                s.delete(old)
            n_ok = 0
            for d in s.exec(select(Detection).where(Detection.capture_id == cap.id)).all():
                loc = localize(d.polygons, (cap.image_width, cap.image_height), vis, cam, xyz)
                if loc is None:
                    continue
                d3 = Damage3D(capture_id=cap.id, detection_id=d.id, job_id=job.id, frame="splat",
                              damage_class=d.damage_class, confidence=d.confidence,
                              centroid=loc.centroid, bbox_min=loc.bbox_min, bbox_max=loc.bbox_max,
                              normal=loc.normal, n_points=loc.n_points, mean_depth=loc.mean_depth,
                              area_est=loc.area_est)
                d3.indices_key = f"captures/{cap.id}/damage3d_{d3.id}_indices.json"
                storage.put_bytes(d3.indices_key, json.dumps(loc.indices.tolist()).encode())
                d3.points_key = f"captures/{cap.id}/damage3d_{d3.id}_points.json"
                _store_points(d3.points_key, xyz[loc.indices])
                s.add(d3)
                n_ok += 1
            if scene_done:
                s.commit()
            else:
                _set_status(s, cap, CaptureStatus.completed,
                            None if n_ok else "no detection could be projected into the splat")
        except Exception as e:
            log.exception("localization failed for job %s", job_id)
            if scene_done:
                s.rollback()
            else:
                _set_status(s, cap, CaptureStatus.failed, f"localization: {e}")
