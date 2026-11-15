"""3D outputs a robot (or a person) can load from elevation.npz:

* ``elevation_mesh.ply``     triangle mesh of the ground surface, per-vertex color = traversable / blocked
* ``elevation_cloud.pcd``    ASCII PCD (PCL / ROS) with x y z + traversable + obstacle_h + slope fields
* ``elevation_3d.html``      standalone interactive viewer (three.js r128 and data embedded, works offline)
Frame: floor-aligned, z up, floor = 0, metres (same as map.yaml).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np

HTML = """<!doctype html><meta charset=utf-8><title>DeepSplat elevation map 3D</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>html,body{margin:0;height:100%;background:#0b1220;color:#e2e8f0;font:14px system-ui}
#ui{position:fixed;top:12px;left:12px;background:#0f172acc;padding:12px 14px;border-radius:10px;max-width:290px;line-height:1.5}
button{margin:2px 4px 2px 0;padding:5px 10px;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;cursor:pointer}
button.on{background:#0ea5e9;border-color:#0ea5e9}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}</style>
<div id=ui><b>DeepSplat elevation map</b><br>__INFO__<br>
<button id=bT class=on>Traversable</button><button id=bH>Height</button><button id=bS>Slope</button><br>
<span class=sw style="background:#22c55e"></span>robot can drive &nbsp;<span class=sw style="background:#ef4444"></span>blocked<br>
<label><input type=checkbox id=cw> wireframe</label> &nbsp; <small>drag = rotate, wheel = zoom, right-drag = pan</small></div>
__THREE_SCRIPTS__
<script>
const D=__DATA__;
const f32=b=>new Float32Array(Uint8Array.from(atob(b),c=>c.charCodeAt(0)).buffer);
const u32=b=>new Uint32Array(Uint8Array.from(atob(b),c=>c.charCodeAt(0)).buffer);
const pos=f32(D.pos),idx=u32(D.idx),cols={T:f32(D.cT),H:f32(D.cH),S:f32(D.cS)};
// map frame is z-up; three.js is y-up: (x,y,z)->(x,z,-y)
const p=new Float32Array(pos.length);for(let i=0;i<pos.length;i+=3){p[i]=pos[i];p[i+1]=pos[i+2];p[i+2]=-pos[i+1];}
const scene=new THREE.Scene();scene.background=new THREE.Color(0x0b1220);
const cam=new THREE.PerspectiveCamera(50,innerWidth/innerHeight,0.05,500);
const r=new THREE.WebGLRenderer({antialias:true});r.setSize(innerWidth,innerHeight);document.body.appendChild(r.domElement);
const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(p,3));
g.setAttribute('color',new THREE.BufferAttribute(cols.T.slice(),3));g.setIndex(new THREE.BufferAttribute(idx,1));g.computeVertexNormals();
const m=new THREE.Mesh(g,new THREE.MeshStandardMaterial({vertexColors:true,side:THREE.DoubleSide,flatShading:false,roughness:.9}));scene.add(m);
scene.add(new THREE.HemisphereLight(0xffffff,0x334155,0.9));const L=new THREE.DirectionalLight(0xffffff,0.8);L.position.set(5,12,6);scene.add(L);
const gr=new THREE.GridHelper(40,40,0x475569,0x1e293b);gr.position.y=-0.01;scene.add(gr);scene.add(new THREE.AxesHelper(2));
const c=D.center;cam.position.set(c[0]+D.span*0.7,D.span*0.8,-c[1]+D.span*0.9);
const o=new THREE.OrbitControls(cam,r.domElement);o.target.set(c[0],0.5,-c[1]);o.update();
function show(k,b){g.attributes.color.array.set(cols[k]);g.attributes.color.needsUpdate=true;for(const x of document.querySelectorAll('button'))x.classList.remove('on');b.classList.add('on');}
bT.onclick=()=>show('T',bT);bH.onclick=()=>show('H',bH);bS.onclick=()=>show('S',bS);cw.onchange=()=>m.material.wireframe=cw.checked;
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();r.setSize(innerWidth,innerHeight)});
(function a(){requestAnimationFrame(a);r.render(scene,cam)})();
</script>"""


VENDOR = Path(__file__).parent / "vendor"


def three_scripts() -> str:
    """three.js r128 + OrbitControls inlined (elevmap/vendor, MIT), so the file needs no internet."""
    return "".join(f"<script>\n{(VENDOR / f).read_text()}\n</script>\n" for f in ("three.min.js", "OrbitControls.js"))


def _ramp(v, lo, hi):
    """viridis-like ramp without matplotlib (dark blue -> teal -> yellow)."""
    t = np.clip((v - lo) / max(hi - lo, 1e-6), 0, 1)[:, None]
    a, b, c = np.array([0.27, 0.0, 0.33]), np.array([0.13, 0.57, 0.55]), np.array([0.99, 0.91, 0.14])
    return np.where(t < 0.5, a + (b - a) * (t * 2), b + (c - b) * ((t - 0.5) * 2)).astype(np.float32)


def build(npz_path: Path, out: Path, stride: int = 2, max_edge_jump: float = 0.6):
    z = np.load(npz_path)
    ground, trav, obst, slope = z["ground"], z["trav"], z["obstacle"], z["slope"]
    ox, oy = [float(v) for v in z["origin_xy"]]
    res = float(z["res"]) * stride
    H0, W0 = ground.shape
    gs = ground[::stride, ::stride]
    tv = trav[::stride, ::stride]
    sl = slope[::stride, ::stride]
    ob = obst[::stride, ::stride]
    H, W = gs.shape
    ok = ~np.isnan(gs)
    vid = -np.ones((H, W), np.int64)
    vid[ok] = np.arange(ok.sum())
    rr, cc = np.nonzero(ok)
    x = ox + (cc * stride + 0.5) * float(z["res"])
    y = oy + (H0 - rr * stride - 0.5) * float(z["res"])
    zz = gs[rr, cc]
    pos = np.c_[x, y, zz].astype(np.float32)
    quad = ok[:-1, :-1] & ok[1:, :-1] & ok[:-1, 1:] & ok[1:, 1:]
    g00, g10, g01, g11 = gs[:-1, :-1], gs[1:, :-1], gs[:-1, 1:], gs[1:, 1:]
    stack = np.stack([g00, g10, g01, g11])
    quad &= (np.nanmax(stack, 0) - np.nanmin(stack, 0)) < max_edge_jump      # no fake walls over steps / gaps
    qi, qj = np.nonzero(quad)
    a, b, c, d = vid[qi, qj], vid[qi + 1, qj], vid[qi, qj + 1], vid[qi + 1, qj + 1]
    tris = np.r_[np.c_[a, b, c], np.c_[b, d, c]].astype(np.uint32)

    t = tv[rr, cc]
    cT = np.where(t[:, None], [0.13, 0.77, 0.37], [0.94, 0.27, 0.27]).astype(np.float32)
    cT = np.where((~t & (ob[rr, cc] == 0))[:, None], [0.85, 0.55, 0.20], cT).astype(np.float32)   # blocked by slope/step only
    cH = _ramp(zz, 0, float(np.nanmax(gs)))
    cS = _ramp(np.nan_to_num(sl[rr, cc]), 0, 30)

    out.mkdir(parents=True, exist_ok=True)
    # PLY mesh
    with open(out / "elevation_mesh.ply", "wb") as f:
        f.write((f"ply\nformat binary_little_endian 1.0\ncomment frame: floor-aligned z-up metres\nelement vertex {len(pos)}\n"
                 "property float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\n"
                 f"element face {len(tris)}\nproperty list uchar int vertex_indices\nend_header\n").encode())
        vtx = np.zeros(len(pos), dtype=[("p", "<f4", 3), ("c", "u1", 3)])
        vtx["p"], vtx["c"] = pos, (cT * 255).astype(np.uint8)
        f.write(vtx.tobytes())
        fc = np.zeros(len(tris), dtype=[("n", "u1"), ("i", "<i4", 3)])
        fc["n"], fc["i"] = 3, tris
        f.write(fc.tobytes())
    # PCD point cloud (full resolution ground cells)
    rr2, cc2 = np.nonzero(~np.isnan(ground))
    px = ox + (cc2 + 0.5) * float(z["res"])
    py = oy + (H0 - rr2 - 0.5) * float(z["res"])
    arr = np.c_[px, py, ground[rr2, cc2], trav[rr2, cc2].astype(float), obst[rr2, cc2], np.nan_to_num(slope[rr2, cc2])]
    with open(out / "elevation_cloud.pcd", "w") as f:
        f.write("# .PCD v0.7\nVERSION 0.7\nFIELDS x y z traversable obstacle_h slope_deg\nSIZE 4 4 4 4 4 4\nTYPE F F F F F F\n"
                f"COUNT 1 1 1 1 1 1\nWIDTH {len(arr)}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS {len(arr)}\nDATA ascii\n")
        np.savetxt(f, arr, fmt="%.3f")
    # HTML viewer
    b64 = lambda a: base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
    span = float(max(x.max() - x.min(), y.max() - y.min()))
    info = (f"{len(pos):,} vertices · {len(tris):,} triangles<br>{float(trav.sum()) * float(z['res']) ** 2:.0f} m² traversable "
            f"· floor = 0 m<br>frame: z up, metres (same as map.yaml)")
    data = {"pos": b64(pos), "idx": b64(tris), "cT": b64(cT), "cH": b64(cH), "cS": b64(cS),
            "center": [float(x.mean()), float(y.mean())], "span": span}
    html = HTML.replace("__DATA__", json.dumps(data)).replace("__INFO__", info)
    (out / "elevation_3d.html").write_text(html.replace("__THREE_SCRIPTS__", three_scripts()))
    return {"vertices": int(len(pos)), "triangles": int(len(tris)), "html_mb": round((out / "elevation_3d.html").stat().st_size / 1e6, 1)}
