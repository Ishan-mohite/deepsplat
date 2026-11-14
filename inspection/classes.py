"""Damage classes with the SAM 3 text prompts used to find them.

Order and labels follow the imoscan UX (Crack, Mould, Stain, Scratch first), the project README
adds the industrial ones. `enabled` classes are sent to the detector. Prompts for crack, mould,
stain, coating damage and corrosion were checked on real photos (tools/run_sam3.py); scratch and
hole are enabled but barely tested. Damage concepts score much lower than everyday objects in
SAM 3 (0.3-0.9), hence the thresholds. `size_metric` decides what "growing" means;
`base_severity` feeds severity.py. Users can correct the type ("detected by an open model").
"""

DAMAGE_CLASSES: dict[str, dict] = {
    "crack": {
        "label": "Crack", "prompts": ["crack"], "min_conf": 0.45,
        "enabled": True, "size_metric": "length", "base_severity": "medium",
    },
    "mould": {
        "label": "Mould", "prompts": ["mold", "black mold"], "min_conf": 0.35,
        "enabled": True, "size_metric": "area", "base_severity": "high",
    },
    "stain": {
        "label": "Stain", "prompts": ["water stain", "stain"], "min_conf": 0.35,
        "enabled": True, "size_metric": "area", "base_severity": "low",
    },
    "scratch": {
        "label": "Scratch", "prompts": ["scratch"], "min_conf": 0.5,
        "enabled": True, "size_metric": "length", "base_severity": "low",
    },
    "coating_damage": {
        "label": "Peeling paint", "prompts": ["peeling paint", "damaged plaster"], "min_conf": 0.4,
        "enabled": True, "size_metric": "area", "base_severity": "low",
    },
    "hole": {
        "label": "Hole", "prompts": ["hole in wall"], "min_conf": 0.45,
        "enabled": True, "size_metric": "area", "base_severity": "medium",
    },
    "corrosion": {
        "label": "Corrosion / rust", "prompts": ["rust"], "min_conf": 0.45,
        "enabled": True, "size_metric": "area", "base_severity": "medium",
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
