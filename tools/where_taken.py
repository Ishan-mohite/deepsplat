"""Show where a localized capture was taken, without touching the splat (it is only read).

    uv run python tools/where_taken.py <capture-id>
Writes (data/blobs, served by the backend under /files/):
  where_<id8>.png   floor plan with the camera position + viewing direction (photo = red, frames = blue)
  where_<id8>.html  interactive 3D: the map's splat with the camera frusta drawn on top
                    (three.js / Spark from the backend's /viewer/vendor/, no internet needed)
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from sqlmodel import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.db import engine  # noqa: E402
from backend.models import Capture, Map  # noqa: E402
from backend.storage import storage  # noqa: E402

HTML = """<!doctype html><meta charset=utf-8><title>where was it taken</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>html,body{margin:0;height:100%;background:#0b1220;color:#e2e8f0;font:14px system-ui;overflow:hidden}
#ui{position:fixed;top:10px;left:10px;background:#0f172acc;padding:10px 12px;border-radius:10px;max-width:300px;line-height:1.5}
button{margin:2px 4px 2px 0;padding:4px 9px;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;cursor:pointer}
#msg{margin-top:6px;color:#94a3b8}</style>
<div id=ui><b>__TITLE__</b><br><span style="color:#ef4444">red</span> = photo camera, <span style="color:#38bdf8">blue</span> = frames.<br>
<button id=bPhoto>View from photo camera</button><button id=bOverview>Overview</button><div id=msg>Loading the splat…</div></div>
<script type="importmap">{"imports":{"three":"/viewer/vendor/three/build/three.module.js","three/addons/":"/viewer/vendor/three/examples/jsm/","@sparkjsdev/spark":"/viewer/vendor/spark/spark.module.js"}}</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';
const D = __DATA__;
const msg = document.getElementById('msg');
const renderer = new THREE.WebGLRenderer({ antialias: false });
renderer.setSize(innerWidth, innerHeight); document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x0b1220);
const cam = new THREE.PerspectiveCamera(60, innerWidth / innerHeight, 0.05, 500);
scene.add(new SparkRenderer({ renderer }));
// map frame is z-up; three.js is y-up: (x,y,z) -> (x,z,-y). The splat file itself is not modified.
const M = new THREE.Matrix4().set(1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1);
const splat = new SplatMesh({ url: D.splat_url, onProgress: e => { if (e?.lengthComputable) msg.textContent = 'Loading the splat… ' + Math.round(e.loaded / e.total * 100) + ' %'; } });
splat.applyMatrix4(M); scene.add(splat);
splat.initialized.then(() => msg.textContent = 'Splat loaded.').catch(e => msg.textContent = 'Splat failed to load: ' + e);
const toV = p => new THREE.Vector3(p[0], p[2], -p[1]);
for (const c of D.cams) {
  const col = c.photo ? 0xef4444 : 0x38bdf8, o = toV(c.center);
  const pts = []; const corners = c.corners.map(toV);
  for (const k of corners) pts.push(o, k);
  for (let i = 0; i < 4; i++) pts.push(corners[i], corners[(i + 1) % 4]);
  scene.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: col })));
  const s = new THREE.Mesh(new THREE.SphereGeometry(c.photo ? 0.09 : 0.05), new THREE.MeshBasicMaterial({ color: col })); s.position.copy(o); scene.add(s);
}
const photo = D.cams.find(c => c.photo), pc = toV(photo.center);
const controls = new OrbitControls(cam, renderer.domElement);
function overview() { cam.fov = 60; cam.updateProjectionMatrix(); cam.position.set(pc.x + 4, pc.y + 5, pc.z + 6); controls.target.copy(pc); controls.update(); }
function fromPhoto() {
  cam.fov = photo.fov_deg; cam.updateProjectionMatrix(); cam.position.copy(pc);
  const f = toV(photo.center.map((v, i) => v + photo.forward[i])); controls.target.copy(f); controls.update(); cam.lookAt(f);
}
bOverview.onclick = overview; bPhoto.onclick = fromPhoto; overview();
addEventListener('resize', () => { cam.aspect = innerWidth / innerHeight; cam.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); });
renderer.setAnimationLoop(() => renderer.render(scene, cam));
</script>"""


def corners(c2w, K, depth=0.7):
    pts = []
    for u, v in ((0, 0), (K["width"], 0), (K["width"], K["height"]), (0, K["height"])):
        d = np.array([(u - K["cx"]) / K["fx"], (v - K["cy"]) / K["fy"], 1.0]) * depth
        pts.append((c2w[:3, :3] @ d + c2w[:3, 3]).tolist())
    return pts


def main(cid: str):
    with Session(engine) as s:
        cap = s.get(Capture, cid)
        m = s.get(Map, cap.map_id)
        loc = cap.localization or {}
        assert loc.get("camera"), "capture is not localized"
        T = np.asarray((loc.get("alignment") or {}).get("matrix", np.eye(4)))
        cams = [{"photo": True, "c2w": np.asarray(loc["camera"]["matrix"]), "K": loc["camera"]}]
        if loc.get("method") == "vps_frames":
            for f in cap.frames:
                if f.get("c2w") is not None:
                    cams.append({"photo": False, "c2w": T @ np.asarray(f["c2w"]), "K": f["K"]})
        fp, fz, fkey = m.floor_plan, m.floor_z, m.floor_plan_key
        map_id = m.id
    out = storage.local_path(f"where_{cid[:8]}.png").parent
    # --- floor plan marker
    img = Image.open(storage.local_path(fkey)).convert("RGB")
    d = ImageDraw.Draw(img)
    px = lambda p: ((p[0] - fp["origin_x"]) / fp["m_per_px"], (fp["origin_y"] - p[1]) / fp["m_per_px"])
    for c in sorted(cams, key=lambda c: c["photo"]):
        x, y = px(c["c2w"][:3, 3])
        fwd = c["c2w"][:3, 2]
        n = np.hypot(fwd[0], fwd[1]) or 1
        ex, ey = x + 70 * fwd[0] / n, y - 70 * fwd[1] / n
        col = (239, 68, 68) if c["photo"] else (56, 189, 248)
        r = 11 if c["photo"] else 6
        d.line([x, y, ex, ey], fill=col, width=4 if c["photo"] else 2)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=(255, 255, 255), width=2)
    ph = cams[0]["c2w"][:3, 3]
    d.text((px(ph)[0] + 16, px(ph)[1] - 8), f"photo  height {ph[2] - fz:.2f} m above floor", fill=(255, 255, 255))
    png = out / f"where_{cid[:8]}.png"
    img.save(png)
    # --- 3D page
    data = {"splat_url": f"maps/{map_id}/splat_viewer.ply", "cams": []}
    for c in cams:
        K = c["K"]
        data["cams"].append({"photo": c["photo"], "center": c["c2w"][:3, 3].tolist(), "corners": corners(c["c2w"], K),
                             "forward": c["c2w"][:3, 2].tolist(), "fov_deg": float(np.degrees(2 * np.arctan(K["height"] / (2 * K["fy"]))))})
    html = out / f"where_{cid[:8]}.html"
    html.write_text(HTML.replace("__DATA__", json.dumps(data)).replace("__TITLE__", f"Capture {cid[:8]} — where it was taken"))
    print("camera (map frame):", np.round(ph, 2), "| height above floor", round(float(ph[2] - fz), 2), "m")
    print("wrote", png, "and", html)
    print(f"3D page: http://localhost:8000/files/{html.name}")


if __name__ == "__main__":
    main(sys.argv[1])
