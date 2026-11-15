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


def alignment_from_frame_poses(device_c2w: list, map_c2w: list, weights: list | None = None,
                               max_dev_m: float = 0.10, max_dev_deg: float = 3.0) -> dict:
    """Combine per-frame estimates into one device->map transform.

    Both pose lists are camera->world with the same camera axes. Rotation: chordal mean,
    translation: median; frames deviating from the consensus by more than max_dev_m / max_dev_deg
    are outliers. `confidence` = inlier share, scaled down when fewer than 3 frames agree.
    """
    A = [np.asarray(a, dtype=np.float64) for a in device_c2w]
    M = [np.asarray(m, dtype=np.float64) for m in map_c2w]
    if not A or len(A) != len(M) or any(x.shape != (4, 4) for x in A + M):
        raise ValueError("need matching lists of 4x4 camera->world poses")
    Ts = [m @ np.linalg.inv(a) for a, m in zip(A, M)]
    T = _consensus(Ts)
    dev = [(float(np.linalg.norm(Ti[:3, 3] - T[:3, 3])), _rot_angle_deg(T[:3, :3].T @ Ti[:3, :3])) for Ti in Ts]
    inl = [i for i, (dm, dd) in enumerate(dev) if dm <= max_dev_m and dd <= max_dev_deg]
    if len(inl) >= 2:
        T = _consensus([Ts[i] for i in inl])
    n = len(Ts)
    confidence = (len(inl) / n) * min(1.0, len(inl) / 3)
    return {
        "matrix": T.round(6).tolist(), "method": "frame_poses",
        "n_frames": n, "n_inliers": len(inl), "inlier_frames": inl,
        "max_dev_m": round(max(d for d, _ in dev), 4), "max_dev_deg": round(max(d for _, d in dev), 3),
        "confidence": round(confidence, 3),
    }
