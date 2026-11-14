"""Align an ARKit capture with the pre-scanned building point cloud (LiDAR scanner, .ply).

Both sides are metric and gravity-aligned (scanners level themselves, ARKit knows "up"), so the
transform is essentially a rotation about the vertical axis plus a translation.

Pipeline:  ARKit cloud (y-up) --A--> z-up  --global (FPFH + RANSAC)--> --ICP point-to-plane-->  scene
Result:    T_arkit_to_scene (4x4), with fitness / RMSE so callers can judge the quality.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import open3d as o3d

# ARKit world: x right, y up, z towards the viewer.  Scanner: z up.  (x, y, z) -> (x, -z, y)
ARKIT_TO_ZUP = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)

REG_VOXEL = 0.05      # m, global registration
ICP_VOXEL = 0.02      # m, fine alignment
PROJ_VOXEL = 0.01     # m, cloud used for projecting damage masks
PREVIEW_VOXEL = 0.03  # m, cloud served to the frontend


MIN_FITNESS = 0.75        # correct alignments score ~1.0 on synthetic data, wrong ones <= 0.65
AMBIGUITY_MARGIN = 0.1    # a clearly different pose within this fitness of the best -> ambiguous


@dataclass
class Registration:
    matrix: list[list[float]]   # ARKit world -> scene
    fitness: float              # share of capture points with a scan point within 4 cm after ICP
    rmse: float                 # m, of those inlier pairs
    method: str                 # "global" | "yaw_sweep" | "icp" | "manual" | "correspondences" | "session"
    max_tilt_deg: float         # deviation from a pure vertical-axis rotation (sanity check)
    ambiguous: bool = False     # another, clearly different pose fits almost as well
    n_candidates: int = 1

    @property
    def ok(self) -> bool:
        return (self.fitness >= MIN_FITNESS and self.rmse <= 0.05 and self.max_tilt_deg <= 5
                and not self.ambiguous)


def to_pcd(xyz: np.ndarray) -> o3d.geometry.PointCloud:
    return o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(xyz, dtype=np.float64)))


def up_matrix(up_axis: str) -> np.ndarray:
    """Matrix taking z-up coordinates into the scene's own convention."""
    if up_axis == "z":
        return np.eye(4)
    if up_axis == "y":
        return np.linalg.inv(ARKIT_TO_ZUP)
    raise ValueError(f"unsupported up_axis {up_axis!r}")


def tilt_deg(T: np.ndarray, up_axis: str = "z") -> float:
    """Angle between ARKit's up (y) mapped through T and the scene's up axis."""
    up_arkit = np.array([0.0, 1.0, 0.0])
    up_scene = np.array([0.0, 0.0, 1.0]) if up_axis == "z" else np.array([0.0, 1.0, 0.0])
    v = T[:3, :3] @ up_arkit
    return float(np.degrees(np.arccos(np.clip(np.dot(v, up_scene) / np.linalg.norm(v), -1, 1))))


def _features(pcd: o3d.geometry.PointCloud, voxel: float):
    down = pcd.voxel_down_sample(voxel)
    down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 2, max_nn=30))
    fpfh = o3d.pipelines.registration.compute_fpfh_feature(
        down, o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 5, max_nn=100))
    return down, fpfh


def _crop(scene: o3d.geometry.PointCloud, center, radius) -> o3d.geometry.PointCloud:
    if center is None or radius is None:
        return scene
    c, r = np.asarray(center, dtype=np.float64), float(radius)
    return scene.crop(o3d.geometry.AxisAlignedBoundingBox(c - r, c + r))


def _rot_z(deg: float) -> np.ndarray:
    a = np.radians(deg)
    T = np.eye(4)
    T[:2, :2] = [[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]
    return T


def _with_normals(pcd: o3d.geometry.PointCloud, voxel: float) -> o3d.geometry.PointCloud:
    d = pcd.voxel_down_sample(voxel)
    d.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 3, max_nn=30))
    return d


def _global_candidates(src_zup, scene_zup, seed: int) -> list[np.ndarray]:
    """FPFH-based guesses (FGR + RANSAC), in z-up coordinates."""
    reg = o3d.pipelines.registration
    s_down, s_f = _features(src_zup, REG_VOXEL)
    t_down, t_f = _features(scene_zup, REG_VOXEL)
    dist = REG_VOXEL * 1.5
    out = []
    fgr = reg.registration_fgr_based_on_feature_matching(
        s_down, t_down, s_f, t_f, reg.FastGlobalRegistrationOption(maximum_correspondence_distance=dist))
    out.append(np.asarray(fgr.transformation))
    o3d.utility.random.seed(seed)
    ran = reg.registration_ransac_based_on_feature_matching(
        s_down, t_down, s_f, t_f, False, dist, reg.TransformationEstimationPointToPoint(False), 3,
        [reg.CorrespondenceCheckerBasedOnEdgeLength(0.9), reg.CorrespondenceCheckerBasedOnDistance(dist)],
        reg.RANSACConvergenceCriteria(50000, 0.999))
    out.append(np.asarray(ran.transformation))
    return out


class _Occupancy:
    """Fast 'how many points land on the scan' score: occupied voxels (dilated by one voxel)."""

    def __init__(self, pts: np.ndarray, voxel: float):
        self.voxel = voxel
        self.origin = pts.min(axis=0) - 2 * voxel
        k = np.floor((pts - self.origin) / voxel).astype(np.int64)
        offs = np.array([[dx, dy, dz] for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)])
        k = (k[:, None, :] + offs[None]).reshape(-1, 3)
        self.dims = k.max(axis=0) + 2
        self.keys = np.unique(self._key(k))

    def _key(self, k: np.ndarray) -> np.ndarray:
        return (k[:, 0] * self.dims[1] + k[:, 1]) * self.dims[2] + k[:, 2]

    def score(self, pts: np.ndarray) -> float:
        k = np.floor((pts - self.origin) / self.voxel).astype(np.int64)
        valid = (k >= 0).all(axis=1) & (k < self.dims).all(axis=1)
        if not valid.any():
            return 0.0
        q = self._key(k[valid])
        i = np.searchsorted(self.keys, q).clip(max=len(self.keys) - 1)
        return float((self.keys[i] == q).sum()) / len(pts)


def _yaw_sweep_candidates(src_zup, scene_zup, center, radius, yaw_step=10, pos_step=0.5,
                          n_icp=30, keep=6):
    """Brute force over yaw x position around a rough location (both clouds gravity-aligned).
    Height is initialized by matching the floors (lowest points). Every start pose is scored
    with a voxel-occupancy test; only the best `n_icp` get a coarse ICP."""
    reg = o3d.pipelines.registration
    src = src_zup.voxel_down_sample(0.1)
    tgt = _with_normals(scene_zup, 0.1)
    s_pts, t_pts = np.asarray(src.points), np.asarray(tgt.points)
    occ = _Occupancy(t_pts, 0.1)
    dz = np.percentile(t_pts[:, 2], 2) - np.percentile(s_pts[:, 2], 2)
    s_c = s_pts - s_pts.mean(axis=0)
    r = min(float(radius), 3.0)
    offsets = np.arange(-r, r + 1e-9, pos_step)
    coarse = []
    for yaw in range(0, 360, yaw_step):
        Rz = _rot_z(yaw)[:3, :3]
        rot = s_c @ Rz.T
        for ox in offsets:
            for oy in offsets:
                t = np.array([center[0] + ox, center[1] + oy, s_pts[:, 2].mean() + dz])
                coarse.append((occ.score(rot + t), yaw, t))
    coarse.sort(key=lambda x: -x[0])
    scored = []
    for _, yaw, t in coarse[:n_icp]:
        T = _rot_z(yaw)
        T[:3, 3] = t - T[:3, :3] @ s_pts.mean(axis=0)
        icp = reg.registration_icp(src, tgt, 0.3, T, reg.TransformationEstimationPointToPlane(),
                                   reg.ICPConvergenceCriteria(max_iteration=20))
        ev = reg.evaluate_registration(src, tgt, 0.1, icp.transformation)
        scored.append((ev.fitness, np.asarray(icp.transformation)))
    scored.sort(key=lambda x: -x[0])
    return [T for _, T in scored[:keep]]


def _distinct(A: np.ndarray, B: np.ndarray, trans_m=0.25, rot_deg=5.0) -> bool:
    dt = np.linalg.norm(A[:3, 3] - B[:3, 3])
    Rr = A[:3, :3].T @ B[:3, :3]
    da = np.degrees(np.arccos(np.clip((np.trace(Rr) - 1) / 2, -1, 1)))
    return dt > trans_m or da > rot_deg


def register(capture_xyz: np.ndarray, scene_xyz: np.ndarray, *, up_axis: str = "z",
             init: np.ndarray | None = None, hint_center=None, hint_radius=None,
             seed: int = 0) -> Registration:
    """Align an ARKit capture (ARKit world frame) with the scan (scene frame).

    `init`:        ARKit->scene guess (skips the global search, ICP only)
    `hint_center`: rough capture position in scene coordinates (+ `hint_radius`, m). Enables the
                   yaw/position sweep, which is far more reliable than feature matching alone.
    """
    reg = o3d.pipelines.registration
    U = up_matrix(up_axis)                 # z-up -> scene
    U_inv = np.linalg.inv(U)
    scene_zup = to_pcd(scene_xyz).transform(U_inv)
    hint_zup = None if hint_center is None else (U_inv @ np.r_[np.asarray(hint_center, float), 1])[:3]
    scene_zup = _crop(scene_zup, hint_zup, None if hint_zup is None else (hint_radius or 4.0) + 3.0)
    if len(scene_zup.points) < 100:
        raise ValueError("scene crop contains too few points (check scene_hint)")
    src = to_pcd(capture_xyz)

    # candidates as ARKit -> z-up transforms, labelled by origin
    cands: list[tuple[str, np.ndarray]] = []
    if init is not None:
        cands.append(("icp", U_inv @ np.asarray(init, dtype=np.float64)))
    else:
        src_zup = to_pcd(capture_xyz).transform(ARKIT_TO_ZUP)
        cands += [("global", T @ ARKIT_TO_ZUP) for T in _global_candidates(src_zup, scene_zup, seed)]
        if hint_zup is not None:
            cands += [("yaw_sweep", T @ ARKIT_TO_ZUP) for T in
                      _yaw_sweep_candidates(src_zup, scene_zup, hint_zup, hint_radius or 4.0)]

    s_fine = src.voxel_down_sample(ICP_VOXEL)
    t_fine = _with_normals(scene_zup, ICP_VOXEL)
    results = []
    for label, T in cands:
        for max_dist in (0.25, ICP_VOXEL * 5, ICP_VOXEL * 2):   # coarse -> fine
            icp = reg.registration_icp(s_fine, t_fine, max_dist, T, reg.TransformationEstimationPointToPlane(),
                                       reg.ICPConvergenceCriteria(max_iteration=50))
            T = np.asarray(icp.transformation)
        results.append((icp.fitness, icp.inlier_rmse, label, T))
    results.sort(key=lambda r: -r[0])
    fit, rmse, label, T_zup = results[0]
    ambiguous = any(f >= fit - AMBIGUITY_MARGIN and _distinct(T_zup, T2) for f, _, _, T2 in results[1:])
    T = U @ T_zup
    return Registration(matrix=T.round(6).tolist(), fitness=round(float(fit), 4), rmse=round(float(rmse), 5),
                        method=label, max_tilt_deg=round(tilt_deg(T, up_axis), 3),
                        ambiguous=bool(ambiguous), n_candidates=len(results))


def from_correspondences(arkit_pts, scene_pts, up_axis: str = "z") -> Registration:
    """Rigid transform (Kabsch, no scale) from >= 3 point pairs, e.g. clicked in the frontend."""
    P, Q = np.asarray(arkit_pts, dtype=np.float64), np.asarray(scene_pts, dtype=np.float64)
    if P.shape != Q.shape or P.shape[0] < 3 or P.shape[1] != 3:
        raise ValueError("need >= 3 pairs of 3D points")
    pc, qc = P.mean(0), Q.mean(0)
    U, _, Vt = np.linalg.svd((P - pc).T @ (Q - qc))
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, qc - R @ pc
    resid = np.linalg.norm((P @ R.T + T[:3, 3]) - Q, axis=1)
    return Registration(matrix=T.round(6).tolist(), fitness=1.0, rmse=round(float(np.sqrt((resid ** 2).mean())), 5),
                        method="correspondences", max_tilt_deg=round(tilt_deg(T, up_axis), 3))


def transform_camera(camera: dict, T: np.ndarray) -> dict:
    """Express an ARKit photo camera (camera->world) in the scene frame."""
    c2w = np.asarray(camera["matrix"], dtype=np.float64)
    return {**camera, "convention": "c2w_opengl", "matrix": (np.asarray(T) @ c2w).round(6).tolist()}


# ------------------------------------------------------------------ scene preprocessing

def load_scene_cloud(path: Path) -> o3d.geometry.PointCloud:
    pcd = o3d.io.read_point_cloud(str(path))
    if len(pcd.points) == 0:
        raise ValueError("point cloud is empty or could not be read (expect .ply)")
    return pcd


def preprocess_scene(path: Path, out_dir: Path) -> dict:
    """Write the downsampled clouds the backend works with. Returns stats for the DB."""
    pcd = load_scene_cloud(path)
    n = len(pcd.points)
    proj = pcd.voxel_down_sample(PROJ_VOXEL)
    np.save(out_dir / "proj.npy", np.asarray(proj.points, dtype=np.float32))
    preview = pcd.voxel_down_sample(PREVIEW_VOXEL)
    o3d.io.write_point_cloud(str(out_dir / "preview.ply"), preview, write_ascii=False)
    lo, hi = proj.get_min_bound(), proj.get_max_bound()
    return {"n_points": n, "n_points_proj": len(proj.points), "n_points_preview": len(preview.points),
            "has_colors": pcd.has_colors(), "bounds_min": np.round(lo, 4).tolist(),
            "bounds_max": np.round(hi, 4).tolist()}
