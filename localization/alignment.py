"""Carry VPS results over to the photo through the device's tracking poses.

A close-up of a crack on a plain wall often cannot be localized on its own. The video frames can,
and every frame and the photo share one metric ARKit session frame. For each localized frame

    T_i = C_map_i · C_device_i⁻¹      (device tracking world -> map)

and the photo's map pose is  C_map_photo = T · C_device_photo.
"""

import numpy as np


def _rot_angle_deg(R: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1))))


def _consensus(Ts: list[np.ndarray]) -> np.ndarray:
    U, _, Vt = np.linalg.svd(sum(T[:3, :3] for T in Ts))
    R = U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, np.median([T[:3, 3] for T in Ts], axis=0)
    return T


def _fit(A: list, M: list, idx: list[int]) -> np.ndarray:
    """One device->map transform from the frames in `idx`: chordal-mean rotation, and the translation that
    best places the camera centres (median of p_map - R p_device), so a small rotation error is not
    amplified by the distance to the device world origin."""
    Ts = [M[i] @ np.linalg.inv(A[i]) for i in idx]
    R = _consensus(Ts)[:3, :3]
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = np.median([M[i][:3, 3] - R @ A[i][:3, 3] for i in idx], axis=0)
    return T


def _residuals(T: np.ndarray, A: list, M: list) -> list[tuple[float, float]]:
    """Where each frame lands after applying T, vs where the VPS put it: (metres, degrees)."""
    out = []
    for a, m in zip(A, M):
        p = T @ a
        out.append((float(np.linalg.norm(p[:3, 3] - m[:3, 3])), _rot_angle_deg(p[:3, :3].T @ m[:3, :3])))
    return out


def alignment_from_frame_poses(device_c2w: list, map_c2w: list, weights: list | None = None,
                               max_dev_m: float = 0.30, max_dev_deg: float = 3.0) -> dict:
    """Combine per-frame VPS poses into one device->map transform, robust to wrong frames.

    Both pose lists are camera->world with the same camera axes. Every frame proposes a transform
    T_i = C_map_i . C_device_i^-1; the proposal that the most other frames agree with (camera centre
    within `max_dev_m`, orientation within `max_dev_deg`) wins, and the final transform is refitted on
    that agreeing group. Frames outside the group are outliers.

    Why not average everything: a visually similar but wrong place (repeated seats) gives a confident,
    wrong pose; on a phone capture 1-2 of 6 localized frames can be 1-4 m and 20 degrees off, and an
    average over all frames is then wrong for every frame. Why 0.30 m: frames that genuinely agree
    differed by 0.16-0.28 m on real phone captures (VPS noise on phone images), wrong ones by > 1 m.
    `confidence` = share of localized frames in the group, scaled down when fewer than 3 agree.
    """
    A = [np.asarray(a, dtype=np.float64) for a in device_c2w]
    M = [np.asarray(m, dtype=np.float64) for m in map_c2w]
    if not A or len(A) != len(M) or any(x.shape != (4, 4) for x in A + M):
        raise ValueError("need matching lists of 4x4 camera->world poses")
    n = len(A)

    def agreeing(T):
        return [i for i, (dm, dd) in enumerate(_residuals(T, A, M)) if dm <= max_dev_m and dd <= max_dev_deg]

    best: list[int] = []
    for i in range(n):                                    # each frame proposes a transform
        inl = agreeing(M[i] @ np.linalg.inv(A[i]))
        if len(inl) > len(best):
            best = inl
    T = _fit(A, M, best)
    for _ in range(3):                                    # refit on the group until it is stable
        inl = agreeing(T)
        if len(inl) < len(best) or inl == best:
            break
        best = inl
        T = _fit(A, M, best)
    inl = agreeing(T)
    if len(inl) >= len(best):
        best = inl
    res = _residuals(T, A, M)
    confidence = (len(best) / n) * min(1.0, len(best) / 3)
    return {
        "matrix": T.round(6).tolist(), "method": "frame_poses",
        "n_frames": n, "n_inliers": len(best), "inlier_frames": best,
        "max_dev_m": round(max(d for d, _ in res), 4), "max_dev_deg": round(max(d for _, d in res), 3),
        "inlier_dev_m": round(max((res[i][0] for i in best), default=0.0), 4),
        "confidence": round(confidence, 3),
    }
