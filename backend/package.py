"""Read a capture zip from the iOS app (docs/PLAN.md on mobile-recorder-app, section 9).

    capture.json, frames/000001.jpg ..., photo.jpg, imu/*.csv, log.txt

Images are raw sensor orientation with ARKit poses; they are turned upright here (cameras.upright),
so everything downstream (SAM 3, VPS, lifting, display) works on upright images with matching
intrinsics and poses (camera->world, OpenCV axes, ARKit session frame).
"""

import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from localization.cameras import Frame, Intrinsics, matrix_from_row_major, to_cv, upright

MAX_UNCOMPRESSED = 500 * 1024 * 1024


@dataclass
class CapturePackage:
    manifest: dict
    photo: Frame
    frames: list[Frame] = field(default_factory=list)

    @property
    def captured_at(self) -> datetime | None:
        m = self.manifest
        if "uptime_to_unix_offset" in m and self.photo.t is not None:
            return datetime.fromtimestamp(m["uptime_to_unix_offset"] + self.photo.t, UTC)
        if "created_unix" in m:
            return datetime.fromtimestamp(m["created_unix"], UTC)
        return None


def _raw_frame(rec: dict, name: str) -> Frame:
    """Frame metadata only (image not read yet)."""
    return Frame(
        name=name,
        image=b"",
        K=Intrinsics.from_dict(rec["intrinsics"]),
        c2w=to_cv(matrix_from_row_major(rec["camera_to_world"])) if rec.get("camera_to_world") else None,
        t=rec.get("t"),
        tracking_state=rec.get("tracking_state", "normal"),
    )


def _load(z: zipfile.ZipFile, rec: dict, raw: Frame) -> Frame:
    raw.image = z.read(rec["file"])
    return upright(raw, rec.get("interface_orientation", "portrait"))


def read_package(path: Path, select_frames: Callable[[list[Frame]], list[Frame]] | None = None) -> CapturePackage:
    """`select_frames` picks frames on metadata only; just those images are read and rotated."""
    with zipfile.ZipFile(path) as z:
        total = 0
        for info in z.infolist():
            p = PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"unsafe path in zip: {info.filename}")
            total += info.file_size
        if total > MAX_UNCOMPRESSED:
            raise ValueError("capture zip too large")
        # the app may zip the folder itself (capture_id/...) or its contents
        names = z.namelist()
        manifest_name = next((n for n in names if PurePosixPath(n).name == "capture.json"), None)
        if manifest_name is None:
            raise ValueError("capture.json missing")
        prefix = str(PurePosixPath(manifest_name).parent)
        prefix = "" if prefix == "." else prefix + "/"
        m = json.loads(z.read(manifest_name))
        if m.get("schema_version") != 1:
            raise ValueError(f"unsupported capture schema_version {m.get('schema_version')}")

        def rec_in_zip(rec: dict) -> dict:
            return {**rec, "file": prefix + rec["file"]}

        if not m.get("photo"):
            raise ValueError("capture.json has no photo")
        photo_rec = rec_in_zip(m["photo"])
        photo = _load(z, photo_rec, _raw_frame(photo_rec, "photo"))
        recs = [rec_in_zip(r) for r in ((m.get("video") or {}).get("frames") or [])]
        raws = [_raw_frame(r, Path(r["file"]).stem) for r in recs]
        by_id = {id(f): r for f, r in zip(raws, recs)}
        chosen = select_frames(raws) if select_frames else raws
        frames = [_load(z, by_id[id(f)], f) for f in chosen]
    return CapturePackage(manifest=m, photo=photo, frames=frames)
