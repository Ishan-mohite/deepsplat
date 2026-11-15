from inspection.issues import OpenIssue, change, find_match
from inspection.severity import severity


def test_severity_by_type_size_location():
    assert severity("crack", 0.2, None) == "medium"
    assert severity("crack", 0.8, None) == "high"
    assert severity("crack", 2.0, None) == "critical"
    assert severity("coating_damage", None, 0.05) == "low"
    assert severity("coating_damage", None, 0.05, "Column C-12 (load-bearing)") == "medium"


def test_match_same_type_within_15cm():
    issues = [OpenIssue(1, "crack", [0, 0, 0]), OpenIssue(2, "corrosion", [0.05, 0, 0]),
              OpenIssue(3, "crack", [0.1, 0, 0], object_id="wall-a")]
    assert find_match("crack", [0.02, 0, 0], None, issues).key == 1
    assert find_match("crack", [0.3, 0, 0], None, issues) is None
    assert find_match("corrosion", [0, 0, 0], None, issues).key == 2
    assert find_match("crack", [0.09, 0, 0], "wall-b", [issues[2]]) is None   # other object


def test_change():
    assert change("crack", 0.30, 0.34) == "stable"       # +13 %
    assert change("crack", 0.30, 0.40) == "worsening"
    assert change("crack", 0.05, 0.065) == "stable"      # +30 % but only 1.5 cm
    assert change("stain", 0.010, 0.020) == "worsening"


def test_growing_crack_stays_linked():
    """A 0.5 m crack grows to 1.4 m in one direction: its centroid moves 0.45 m and is 0.2 m
    outside the old extent; only the old centroid inside the new extent links them."""
    old = OpenIssue(1, "crack", [0.25, 0, 1.0], bbox_min=[0.0, -0.01, 1.0], bbox_max=[0.5, 0.01, 1.0])
    assert find_match("crack", [0.7, 0, 1.0], None, [old]) is None                    # without the new extent: miss
    assert find_match("crack", [0.7, 0, 1.0], None, [old],
                      bbox_min=[0.0, -0.01, 1.0], bbox_max=[1.4, 0.01, 1.0]).key == 1   # with it: linked
    far = OpenIssue(2, "crack", [3.0, 0, 1.0], bbox_min=[2.8, 0, 1.0], bbox_max=[3.2, 0, 1.0])
    assert find_match("crack", [0.7, 0, 1.0], None, [far], bbox_min=[0.0, 0, 1.0], bbox_max=[1.4, 0, 1.0]) is None
