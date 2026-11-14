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
