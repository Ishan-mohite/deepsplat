"""Benchmark the visual positioning system against views whose true pose is known.

Protocol (no phone needed, reproducible):
  1. Take the scan's COLMAP views (images + poses). Hold out B blocks of L consecutive timestamps
     (both cameras), plus a margin of G timestamps on each side that is also removed from the index,
     so held-out queries have no near-duplicate neighbours in the map.
  2. Rebuild the SIFT index WITHOUT those views (temporary folder, the live map is untouched).
  3. Single-frame test: localize every held-out view, compare with its known pose.
  4. Multi-frame test: groups of 6 frames 0.5 s apart get a random "ARKit world" transform (+ noise);
     run the backend's own alignment + confidence formula; the result must reproduce the true pose.
  5. Report success rates, error percentiles and how well each confidence score separates right
     from wrong answers (the number that decides whether a threshold is safe).

    uv run python tools/eval_vps.py --colmap <dir with images/ and sparse/0/> --proj data/blobs/maps/auditorium/proj.npz --out eval_out
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localization import sift_vps  # noqa: E402
from localization.alignment import alignment_from_frame_poses  # noqa: E402
from localization.cameras import Frame  # noqa: E402
from localization.vps import multi_frame_confidence  # noqa: E402
from localization.map_views import load_views  # noqa: E402

OK_POS, OK_ROT = 0.25, 5.0       # "correct" = within 25 cm and 5 degrees


def ts_of(name: str) -> int:
    return int(Path(name).stem)


def rot_err_deg(Ra, Rb) -> float:
    return float(np.degrees(np.arccos(np.clip((np.trace(Ra.T @ Rb) - 1) / 2, -1, 1))))


def pose_err(pred_c2w, gt_c2w):
    return float(np.linalg.norm(pred_c2w[:3, 3] - gt_c2w[:3, 3])), rot_err_deg(pred_c2w[:3, :3], gt_c2w[:3, :3])


def write_subset(colmap: Path, keep_names: set[str], dst: Path):
    (dst / "sparse/0").mkdir(parents=True, exist_ok=True)
    shutil.copy(colmap / "sparse/0/cameras.txt", dst / "sparse/0/cameras.txt")
    lines = [l for l in (colmap / "sparse/0/images.txt").read_text().splitlines() if not l.startswith("#")]
    out = []
    for a, b in zip(lines[::2], lines[1::2]):
        if a.split()[9] in keep_names:
            out += [a, b]
    (dst / "sparse/0/images.txt").write_text("\n".join(out) + "\n")
    (dst / "sparse/0/points3D.txt").write_text("")
    img = dst / "images"
    if img.exists() or img.is_symlink():
        img.unlink()
    img.symlink_to((colmap / "images").resolve())


def pct(a, q):
    return round(float(np.percentile(a, q)), 3) if len(a) else None


def confusion(correct: np.ndarray, score: np.ndarray, thr: float):
    acc = score >= thr
    return {"threshold": thr, "accepted": int(acc.sum()), "accepted_correct": int((acc & correct).sum()),
            "accepted_WRONG": int((acc & ~correct).sum()), "rejected_correct": int((~acc & correct).sum()),
            "rejected_wrong": int((~acc & ~correct).sum())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--colmap", required=True, type=Path)
    ap.add_argument("--proj", required=True, type=Path, help="proj.npz with 'xyz' (opaque gaussian centres)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--blocks", type=int, default=8)
    ap.add_argument("--block-len", type=int, default=12)
    ap.add_argument("--gap", type=int, default=2)
    ap.add_argument("--index-features", type=int, default=2000)
    ap.add_argument("--variants", default="baseline", help="comma list: baseline,ratio09,feat8000,ratio09_feat8000")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--outlier-rate", type=float, default=0.25,
                    help="multi-frame test: share of localized frames given a gross wrong pose (real phone frames do this)")
    ap.add_argument("--rot-noise-deg", type=float, default=0.7, help="multi-frame test: device tracking rotation noise")
    ap.add_argument("--repeats", type=int, default=8, help="multi-frame test: random re-draws of world transform / noise / outliers")
    ap.add_argument("--report-name", default="report.json")
    ap.add_argument("--reuse-index", action="store_true", help="skip rebuilding the held-out index if it exists")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)

    image_root, views = load_views(a.colmap)
    byts = sorted({ts_of(v.name) for v in views})
    n = len(byts)
    starts = np.linspace(a.gap + 1, n - a.block_len - a.gap - 1, a.blocks).astype(int)
    held_ts, drop_ts, blocks = set(), set(), []
    for s in starts:
        blk = byts[s:s + a.block_len]
        blocks.append(blk)
        held_ts |= set(blk)
        drop_ts |= set(byts[max(0, s - a.gap):s + a.block_len + a.gap])
    held = [v for v in views if ts_of(v.name) in held_ts]
    index_views = [v for v in views if ts_of(v.name) not in drop_ts]
    print(f"views {len(views)} | held-out queries {len(held)} | indexed {len(index_views)} "
          f"| removed margin {len(views) - len(index_views) - len(held)}", flush=True)

    tmp = a.out / "_subset"
    sift_vps.MAX_FEATURES_INDEX = a.index_features
    vps = sift_vps.SiftVPS(a.out / "_index")
    if a.reuse_index and vps.has_index("eval"):
        print("reusing existing held-out index", flush=True)
    else:
        write_subset(a.colmap, {v.name for v in index_views}, tmp)
        t0 = time.time()
        xyz = np.load(a.proj)["xyz"].astype(np.float64)
        info = vps.build_index("eval", tmp, map_xyz=xyz)
        print(f"index built in {time.time() - t0:.0f}s: {info}", flush=True)

    defaults = dict(RATIO=sift_vps.RATIO, MAX_FEATURES_QUERY=sift_vps.MAX_FEATURES_QUERY)
    variants = {"baseline": {}, "ratio09": {"RATIO": 0.9}, "feat8000": {"MAX_FEATURES_QUERY": 8000},
                "ratio09_feat8000": {"RATIO": 0.9, "MAX_FEATURES_QUERY": 8000}}
    report = {"protocol": vars(a) | {"colmap": str(a.colmap), "proj": str(a.proj), "out": str(a.out)},
              "n_views": len(views), "n_queries": len(held), "n_indexed": len(index_views), "variants": {}}

    for vname in a.variants.split(","):
        for k, v in defaults.items():
            setattr(sift_vps, k, v)
        for k, v in variants[vname].items():
            setattr(sift_vps, k, v)
        vps._cache.clear()
        print(f"\n=== variant {vname} {variants[vname]}", flush=True)
        t0 = time.time()
        frames = [Frame(v.name, (image_root / v.name).read_bytes(), v.K, None) for v in held]
        res = vps.localize("eval", frames)
        dt = time.time() - t0
        rows = []
        for v, r in zip(held, res):
            if r is None:
                rows.append({"name": v.name, "ok": False})
            else:
                pe, re = pose_err(r.c2w, v.c2w)
                rows.append({"name": v.name, "ok": True, "pos_m": pe, "rot_deg": re, "inliers": r.num_inliers,
                             "conf": r.confidence, "correct": pe < OK_POS and re < OK_ROT})
        got = [r for r in rows if r["ok"]]
        correct = np.array([r["correct"] for r in got])
        pos = np.array([r["pos_m"] for r in got])
        rot = np.array([r["rot_deg"] for r in got])
        inl = np.array([r["inliers"] for r in got])
        single = {
            "queries": len(rows), "returned_a_pose": len(got),
            "no_pose_rate": round(1 - len(got) / len(rows), 3),
            "correct_rate_of_all": round(float(correct.sum()) / len(rows), 3),
            "wrong_but_returned": int((~correct).sum()),
            "pos_err_m_pct50_90_99": [pct(pos[correct], 50), pct(pos[correct], 90), pct(pos[correct], 99)] if correct.any() else None,
            "rot_err_deg_pct50_90_99": [pct(rot[correct], 50), pct(rot[correct], 90), pct(rot[correct], 99)] if correct.any() else None,
            "inliers_correct_median": int(np.median(inl[correct])) if correct.any() else None,
            "inliers_wrong_median": int(np.median(inl[~correct])) if (~correct).any() else None,
            "seconds_per_query": round(dt / len(rows), 2),
            "by_inlier_threshold": [confusion(correct, inl.astype(float), t) for t in (15, 25, 40, 60, 100)],
        }
        print(json.dumps(single, indent=1), flush=True)

        # ---- multi-frame simulation (left camera, 6 frames, 0.5 s apart), repeated with random draws
        by_name = {v.name: (v, r) for v, r in zip(held, res)}

        def rand_rot(deg):
            ax = rng.normal(size=3); ax /= np.linalg.norm(ax)
            th = np.radians(deg)
            K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
            return np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * K @ K

        sims = []
        for blk in blocks:
            seq = [v for t in blk for v in held if ts_of(v.name) == t and v.name.startswith("left")]
            for i in range(0, len(seq) - 5):
                grp = seq[i:i + 6]
                for _ in range(a.repeats):
                    yaw = rng.uniform(0, 2 * np.pi)
                    D = np.eye(4)
                    D[:3, :3] = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
                    D[:3, 3] = rng.uniform(-5, 5, 3)
                    dev, mp, rs, ok_idx = [], [], [], []
                    for k, v in enumerate(grp):
                        noise = np.eye(4)
                        noise[:3, 3] = rng.normal(0, 0.01, 3)            # 1 cm tracking noise
                        noise[:3, :3] = rand_rot(abs(rng.normal(0, a.rot_noise_deg)))
                        dev.append(D @ v.c2w @ noise)
                        r = by_name[v.name][1]
                        if r is None:
                            continue
                        c2w = r.c2w.copy()
                        if rng.random() < a.outlier_rate:                  # a confident wrong frame
                            c2w[:3, 3] += rng.normal(size=3) / 1.7 * rng.uniform(1.5, 4.0)
                            c2w[:3, :3] = rand_rot(rng.uniform(10, 30)) @ c2w[:3, :3]
                        mp.append(c2w); rs.append(r); ok_idx.append(k)
                    row = {"n_localized": len(rs)}
                    if rs:
                        al = alignment_from_frame_poses([dev[k] for k in ok_idx], mp)
                        T = np.asarray(al["matrix"])
                        last = grp[-1]
                        pe, re = pose_err(T @ dev[-1], last.c2w)
                        row |= {"pos_m": pe, "rot_deg": re, "agree": al["confidence"], "n_inl": al["n_inliers"],
                                "median_inliers": float(np.median([r.num_inliers for r in rs])),
                                "correct": pe < OK_POS and re < OK_ROT}
                        row["conf_backend"] = multi_frame_confidence(al["confidence"], [r.num_inliers for r in rs])
                    sims.append(row)
        got = [s_ for s_ in sims if "correct" in s_]
        multi = {"groups": len(sims), "groups_with_any_localized_frame": len(got),
                 "outlier_rate": a.outlier_rate, "rot_noise_deg": a.rot_noise_deg}
        if got:
            c = np.array([s_["correct"] for s_ in got])
            cb = np.array([s_["conf_backend"] for s_ in got])
            multi |= {"correct_rate_of_all_groups": round(float(c.sum()) / len(sims), 3),
                      "wrong_but_returned": int((~c).sum()),
                      "pos_err_m_pct50_90_99": [pct(np.array([s_["pos_m"] for s_ in got])[c], q) for q in (50, 90, 99)] if c.any() else None,
                      "confidence>=0.5 (what the backend accepts)": confusion(c, cb, 0.5),
                      "confidence>=0.35": confusion(c, cb, 0.35)}
        print(json.dumps(multi, indent=1), flush=True)
        report["variants"][vname] = {"single_frame": single, "multi_frame": multi, "rows": rows}

    (a.out / a.report_name).write_text(json.dumps(report, indent=1, default=float))
    print(f"\nwrote {a.out / a.report_name}", flush=True)


if __name__ == "__main__":
    main()
