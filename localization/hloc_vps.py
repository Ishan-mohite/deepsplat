"""VPS with hloc (README tech stack): NetVLAD retrieval + SuperPoint / LightGlue + PnP, using the
splat's training images with their known poses.

Index (once per map, GPU strongly recommended):
  1. reference COLMAP model from the known view poses
  2. SuperPoint local features + NetVLAD global descriptors of all views
  3. pairs from poses -> LightGlue matches -> triangulation with fixed poses (3D points)
Query (per frame):
  NetVLAD top-k views -> LightGlue matches -> 2D-3D -> PnP + refinement (pycolmap)

UNTESTED in this repo so far (needs torch + hloc, see requirements-vps.txt). Written against the
hloc master API (extract_features / match_features / pairs_from_poses / pairs_from_retrieval /
triangulation / localize_sfm.pose_from_cluster).
"""

import json
import shutil
import tempfile
import threading
from pathlib import Path

import numpy as np

from .cameras import Frame
from .map_views import load_views, write_colmap_model
from .vps import VpsResult, confidence_from_inliers

FEATURES = "superpoint_max"
GLOBAL = "netvlad"
MATCHER = "superpoint+lightglue"
NUM_POSE_PAIRS = 20
NUM_RETRIEVED = 10
MIN_INLIERS = 30


class HlocVPS:
    name = "hloc"

    def __init__(self, index_root):
        self.root = Path(index_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._models: dict = {}
        self._lock = threading.Lock()

    def _dir(self, map_id: str) -> Path:
        return self.root / map_id

    def has_index(self, map_id: str) -> bool:
        return (self._dir(map_id) / "index.json").is_file()

    def build_index(self, map_id: str, views_dir, views_to_map=None) -> dict:
        from hloc import extract_features, match_features, pairs_from_poses, triangulation

        out = self._dir(map_id)
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        image_root, views = load_views(Path(views_dir), views_to_map)
        names = [v.name for v in views]
        (out / "db_list.txt").write_text("\n".join(names) + "\n")
        ref = out / "reference"
        write_colmap_model(views, ref)

        feat_conf, glob_conf = extract_features.confs[FEATURES], extract_features.confs[GLOBAL]
        features = extract_features.main(feat_conf, image_root, image_list=names,
                                         feature_path=out / "features.h5")
        extract_features.main(glob_conf, image_root, image_list=names, feature_path=out / "global.h5")
        pairs = out / "pairs-poses.txt"
        pairs_from_poses.main(ref, pairs, NUM_POSE_PAIRS)
        matches = match_features.main(match_features.confs[MATCHER], pairs, features=features,
                                      matches=out / "matches.h5")
        model = triangulation.main(out / "sfm", ref, image_root, pairs, features, matches)
        info = {"n_views": len(views), "n_points3D": int(model.num_points3D()),
                "features": FEATURES, "matcher": MATCHER, "global": GLOBAL}
        (out / "index.json").write_text(json.dumps(info))
        self._models.pop(map_id, None)
        return info

    def _model(self, map_id: str):
        import pycolmap
        with self._lock:
            if map_id not in self._models:
                self._models[map_id] = pycolmap.Reconstruction(self._dir(map_id) / "sfm")
            return self._models[map_id]

    def localize(self, map_id: str, frames: list[Frame]) -> list[VpsResult | None]:
        import pycolmap
        from hloc import extract_features, match_features, pairs_from_retrieval
        from hloc.localize_sfm import QueryLocalizer, pose_from_cluster

        if not self.has_index(map_id):
            raise RuntimeError(f"no VPS index for map {map_id}; POST /maps/{map_id}/index first")
        idx = self._dir(map_id)
        model = self._model(map_id)
        name_to_id = {img.name: img_id for img_id, img in model.images.items()}
        results: list[VpsResult | None] = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            qdir = tmp / "query"
            qdir.mkdir()
            qnames = []
            for i, f in enumerate(frames):
                n = f"q{i:03d}.jpg"
                (qdir / n).write_bytes(f.image)
                qnames.append(n)
            feat_conf, glob_conf = extract_features.confs[FEATURES], extract_features.confs[GLOBAL]
            qfeat = extract_features.main(feat_conf, qdir, image_list=qnames, feature_path=tmp / "qf.h5")
            qglob = extract_features.main(glob_conf, qdir, image_list=qnames, feature_path=tmp / "qg.h5")
            pairs = tmp / "pairs.txt"
            pairs_from_retrieval.main(qglob, pairs, NUM_RETRIEVED, db_model=idx / "sfm",
                                      db_descriptors=idx / "global.h5")
            matches = match_features.main(match_features.confs[MATCHER], pairs, features=qfeat,
                                          matches=tmp / "qm.h5", features_ref=idx / "features.h5")
            retrieved: dict[str, list[str]] = {}
            for line in pairs.read_text().splitlines():
                q, db = line.split()
                retrieved.setdefault(q, []).append(db)

            conf = {"estimation": {"ransac": {"max_error": 12.0}}, "refinement": {}}
            localizer = QueryLocalizer(model, conf)
            for n, f in zip(qnames, frames):
                db_ids = [name_to_id[d] for d in retrieved.get(n, []) if d in name_to_id]
                if not db_ids:
                    results.append(None)
                    continue
                cam = pycolmap.Camera(model="PINHOLE", width=f.K.width, height=f.K.height,
                                      params=[f.K.fx, f.K.fy, f.K.cx, f.K.cy])
                try:
                    ret, _ = pose_from_cluster(localizer, n, cam, db_ids, qfeat, matches)
                except Exception:
                    ret = None
                if not ret or ret.get("num_inliers", 0) < MIN_INLIERS:
                    results.append(None)
                    continue
                w2c = np.eye(4)
                w2c[:3, :4] = np.asarray(ret["cam_from_world"].matrix())
                n_in = int(ret["num_inliers"])
                results.append(VpsResult(c2w=np.linalg.inv(w2c), num_inliers=n_in,
                                         confidence=confidence_from_inliers(n_in)))
        return results
