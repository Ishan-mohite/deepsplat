"""Build the dashboard's floor map (elevmap/floormap.py) for maps registered before it existed, or again
after changing it:   uv run python tools/build_floor_map.py [<map-id> ...]     (no id: every ready map)
Run it from the repo root (reads .env); the running backend picks the new map up on its next poll."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlmodel import Session, select  # noqa: E402

from backend import pipeline  # noqa: E402
from backend.db import engine  # noqa: E402
from backend.models import Map, MapStatus  # noqa: E402


def main(ids: list[str]) -> None:
    with Session(engine) as s:
        ids = ids or [m.id for m in s.exec(select(Map).where(Map.status == MapStatus.ready)).all()]
    for mid in ids:
        with Session(engine) as s:
            m = s.get(Map, mid)
            if m is None:
                print(f"{mid}: not found"); continue
            t = time.time()
            pipeline.build_floor_map(m)
            s.add(m); s.commit()
            fm = m.floor_plan["floor_map"]
            print(f"{mid}: {fm['width']}x{fm['height']} px, {fm['area_m2']} m² mapped, "
                  f"{fm['wall_m2']} m² walls ({time.time() - t:.0f}s)")


if __name__ == "__main__":
    main(sys.argv[1:])
