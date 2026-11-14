"""Severity from type, size and location (README step 3)."""

from .classes import DAMAGE_CLASSES

LEVELS = ["low", "medium", "high", "critical"]
STRUCTURAL_KEYWORDS = ("load-bearing", "load bearing", "column", "beam", "pillar", "girder", "structural")

# size steps: one level up above the first value, two above the second
LENGTH_STEPS_M = (0.5, 1.5)
AREA_STEPS_M2 = (0.1, 0.5)


def severity(damage_class: str, length_m: float | None, area_m2: float | None,
             object_label: str | None = None) -> str:
    cfg = DAMAGE_CLASSES.get(damage_class, {})
    level = LEVELS.index(cfg.get("base_severity", "medium"))
    if cfg.get("size_metric") == "length":
        value, steps = length_m, LENGTH_STEPS_M
    else:
        value, steps = area_m2, AREA_STEPS_M2
    if value is not None:
        level += sum(value > s for s in steps)
    if object_label and any(k in object_label.lower() for k in STRUCTURAL_KEYWORDS):
        level += 1
    return LEVELS[min(level, len(LEVELS) - 1)]


def max_severity(a: str, b: str) -> str:
    return LEVELS[max(LEVELS.index(a), LEVELS.index(b))]
