"""Multi-frame pose confidence: agreement between frames decides; inlier counts only need a floor."""
from localization.vps import multi_frame_confidence


def test_consistent_frames_with_modest_inliers_pass():
    # the two phone captures from the real auditorium test: all frames agreed, 41-92 inliers each
    assert multi_frame_confidence(1.0, [58, 92, 84, 66, 45]) == 1.0
    assert multi_frame_confidence(1.0, [65, 61, 70, 57, 43, 41]) == 1.0


def test_disagreeing_frames_are_rejected_whatever_the_inliers():
    # a capture whose frames each matched 76-138 features but disagreed by 1.7 m: agreement 0
    assert multi_frame_confidence(0.0, [81, 76, 108, 138, 119, 118]) == 0.0


def test_few_inliers_scale_down_and_empty_is_zero():
    assert multi_frame_confidence(1.0, [20, 20, 20]) == 0.5
    assert multi_frame_confidence(1.0, []) == 0.0


def test_partial_agreement_scales():
    assert multi_frame_confidence(0.5, [100, 100]) == 0.5
