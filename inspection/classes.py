"""Damage classes from the project README, with the SAM 3 text prompts used to find them.

`enabled` classes are sent to the detector. Only classes that were checked on real images are
enabled (crack, corrosion, leak/stain, coating damage); the others have untested prompts.
Damage concepts score much lower than everyday objects in SAM 3 (0.3-0.9), hence the thresholds.
`size_metric` decides what "growing" means for change tracking; `base_severity` feeds severity.py.
"""

DAMAGE_CLASSES: dict[str, dict] = {
    "crack": {
        "label": "Crack", "prompts": ["crack"], "min_conf": 0.45,
        "enabled": True, "size_metric": "length", "base_severity": "medium",
    },
    "corrosion": {
        "label": "Corrosion / rust", "prompts": ["rust", "corrosion"], "min_conf": 0.45,
        "enabled": True, "size_metric": "area", "base_severity": "medium",
    },
    "leak_stain": {
        "label": "Leak / stain", "prompts": ["water stain", "water damage"], "min_conf": 0.35,
        "enabled": True, "size_metric": "area", "base_severity": "medium",
    },
    "coating_damage": {
        "label": "Coating damage", "prompts": ["peeling paint", "damaged plaster"], "min_conf": 0.4,
        "enabled": True, "size_metric": "area", "base_severity": "low",
    },
    "dent": {
        "label": "Dent / deformation", "prompts": ["dent"], "min_conf": 0.45,
        "enabled": False, "size_metric": "area", "base_severity": "low",
    },
    "exposed_wiring": {
        "label": "Exposed wiring", "prompts": ["exposed electrical wires"], "min_conf": 0.45,
        "enabled": False, "size_metric": "area", "base_severity": "high",
    },
    "obstruction_spill": {
        "label": "Obstruction / spill", "prompts": ["spill on the floor"], "min_conf": 0.45,
        "enabled": False, "size_metric": "area", "base_severity": "medium",
    },
    # Absence cannot be found with a text prompt; needs a reference (map labels or a trained model).
    "missing_part": {
        "label": "Missing or loose part", "prompts": [], "min_conf": 1.0,
        "enabled": False, "size_metric": "area", "base_severity": "high",
    },
}


def enabled_classes() -> dict[str, dict]:
    return {k: v for k, v in DAMAGE_CLASSES.items() if v["enabled"] and v["prompts"]}


def label(cls: str) -> str:
    return DAMAGE_CLASSES.get(cls, {}).get("label", cls)
