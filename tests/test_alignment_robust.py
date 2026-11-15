"""Robust device->map alignment: wrong frames must not corrupt the group of frames that agree."""
import numpy as np

from localization.alignment import alignment_from_frame_poses


def rot(axis, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def pose(R, t):
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, t
    return T


def scene(n=6, seed=0, noise_m=0.06, noise_deg=0.4):
    """n device poses, a true device->map transform, and noisy VPS map poses for them."""
    rng = np.random.default_rng(seed)
    D = pose(rot("z", 70) @ rot("x", 3), [4.0, -2.0, 1.0])               # device world -> map
    A = [pose(rot("y", 5 * i), [0.2 * i, 0.0, 0.1 * i]) for i in range(n)]
    M = []
    for a in A:
        m = D @ a
        m[:3, 3] += rng.normal(0, noise_m, 3)
        m[:3, :3] = rot("z", rng.normal(0, noise_deg)) @ m[:3, :3]
        M.append(m)
    return D, A, M


def test_good_frames_with_phone_level_noise_agree():
    D, A, M = scene()
    al = alignment_from_frame_poses(A, M)
    assert al["n_inliers"] == 6 and al["confidence"] == 1.0
    assert np.linalg.norm(np.asarray(al["matrix"])[:3, 3] - D[:3, 3]) < 0.1


def test_wrong_frames_are_outliers_and_do_not_corrupt_the_rest():
    D, A, M = scene(6)
    M[0] = pose(rot("z", 23) @ M[0][:3, :3], M[0][:3, 3] + [2.0, -3.0, 1.0])     # 23 deg, ~3.7 m off
    M[3] = pose(rot("y", 12) @ M[3][:3, :3], M[3][:3, 3] + [0.9, 0.8, 0.4])
    al = alignment_from_frame_poses(A, M)
    assert al["inlier_frames"] == [1, 2, 4, 5] and al["n_inliers"] == 4
    assert np.linalg.norm(np.asarray(al["matrix"])[:3, 3] - D[:3, 3]) < 0.1       # not dragged by the outliers
    assert al["max_dev_m"] > 1.0                                                  # outliers still reported


def test_old_failure_case_wrong_majority_is_not_accepted():
    # three mutually inconsistent frames: no group of 3 -> low confidence
    D, A, M = scene(3)
    M[0][:3, 3] += [2.0, 0, 0]
    M[1][:3, 3] += [0, 3.0, 0]
    al = alignment_from_frame_poses(A, M)
    assert al["n_inliers"] <= 1 and al["confidence"] < 0.2


def test_two_agreeing_frames_score_two_thirds():
    D, A, M = scene(2)
    al = alignment_from_frame_poses(A, M)
    assert al["n_inliers"] == 2 and al["confidence"] == 0.667


def test_far_device_origin_does_not_break_agreement():
    # device world origin ~36 m from the cameras: a 0.6 deg rotation scatter must not turn into metre-sized "errors"
    rng = np.random.default_rng(3)
    D = pose(rot("z", 70) @ rot("x", 3), [4.0, -2.0, 1.0])
    A = [pose(rot("y", 5 * i), [30.0 + 0.2 * i, 20.0, 0.1 * i]) for i in range(5)]
    M = []
    for a in A:
        m = D @ a
        m[:3, 3] += rng.normal(0, 0.05, 3)
        m[:3, :3] = rot("z", rng.normal(0, 0.6)) @ m[:3, :3]
        M.append(m)
    al = alignment_from_frame_poses(A, M)
    assert al["n_inliers"] == 5 and al["inlier_dev_m"] < 0.3
