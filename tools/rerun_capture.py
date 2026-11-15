"""Re-run the pipeline for captures already uploaded (same as the app's /complete step), e.g. after the
localization settings changed:   uv run python tools/rerun_capture.py <capture-id> [<capture-id> ...]
Initialises the VPS exactly like the server's startup, so run it from the repo root (reads .env)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlmodel import Session  # noqa: E402

from backend import pipeline  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.db import engine  # noqa: E402
from backend.models import Capture, CaptureStatus  # noqa: E402
from localization import vps as vps_mod  # noqa: E402


def main(ids: list[str]) -> None:
    vps_mod.configure(settings.vps, settings.data_dir / "vps")
    if vps_mod.get_vps() is None:
        sys.exit("no VPS configured (VPS=none in .env)")
    for cid in ids:
        with Session(engine) as s:
            cap = s.get(Capture, cid)
            if cap is None:
                print(f"{cid}: not found"); continue
            print(f"{cid[:8]} before: {cap.status} | {cap.error}")
            cap.status = CaptureStatus.uploaded
            s.add(cap); s.commit()
        t = time.time()
        pipeline.process_package(cid)
        with Session(engine) as s:
            cap = s.get(Capture, cid)
            loc = cap.localization or {}
            al = loc.get("alignment") or {}
            print(f"{cid[:8]} after ({time.time() - t:.0f}s): {cap.status} | {cap.error}")
            print(f"   localization: {loc.get('method')} confidence {loc.get('confidence')} {loc.get('error') or ''}")
            if al:
                print(f"   frames agreeing: {al['n_inliers']}/{al['n_frames']} (max dev {al['max_dev_m']} m, {al['max_dev_deg']} deg)")


if __name__ == "__main__":
    main(sys.argv[1:])
