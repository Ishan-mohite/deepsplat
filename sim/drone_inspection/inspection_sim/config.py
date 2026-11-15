from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import math

ROOT = Path(__file__).resolve().parents[1]

@dataclass
class Config:
    scene: str = "assets/scenes/demo_room.glb"
    scale_to_meters: float = 1.0
    source_up: str = "Y"
    renderer: str = "habitat"
    initial_position: list = field(default_factory=lambda: [0.0, 0.0, 1.3])
    initial_quaternion_xyzw: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    physics_hz: int = 240
    control_hz: int = 48
    render_hz: int = 8
    max_speed: float = 0.6
    max_acceleration: float = 0.8
    max_yaw_rate: float = 0.8
    clearance: float = 0.18
    command_timeout: float = 0.5
    horizontal_fov: float = 90.0
    resolution: list = field(default_factory=lambda: [480, 360])
    grid_resolution: float = 0.35
    max_grid_cells: int = 150000
    surface_samples: int = 900
    target_bounds: list = field(default_factory=lambda: [[-3.001, -3.001, -0.001], [3.001, 3.001, 3.001]])
    distance_range: list = field(default_factory=lambda: [0.35, 3.5])
    max_view_angle: float = 75.0
    minimum_pixels_per_meter: float = 45.0
    coverage_threshold: float = 0.98
    max_sim_seconds: float = 180.0
    max_planner_steps: int = 400
    selection_radius: float = 0.65
    seed: int = 17

    @classmethod
    def load(cls, path=None):
        values = json.loads(Path(path).read_text()) if path else {}
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown: raise ValueError("Unknown configuration fields: " + ", ".join(sorted(unknown)))
        config = cls(**values)
        config.validate()
        return config

    def validate(self):
        scalars = [v for v in asdict(self).values() if isinstance(v, (int, float))]
        if not all(math.isfinite(v) and v > 0 for v in scalars if v != self.seed):
            raise ValueError("Numeric settings must be finite and positive")
        if self.source_up not in ("Y", "Z") or self.renderer not in ("habitat", "bullet"):
            raise ValueError("source_up must be Y/Z and renderer habitat/bullet")
        if self.physics_hz % self.control_hz: raise ValueError("physics_hz must be divisible by control_hz")
        if not 1 <= self.render_hz <= 30 or not 0 < self.max_speed <= 3:
            raise ValueError("Invalid render rate or speed")
        if not 30 <= self.horizontal_fov <= 120: raise ValueError("FOV must be 30–120 degrees")
        w,h = self.resolution
        if int(w)!=w or int(h)!=h or not 64<=w<=1280 or not 64<=h<=960: raise ValueError("Invalid image resolution")
        if not 0 < self.coverage_threshold <= 1 or not 0 < self.max_view_angle < 90: raise ValueError("Invalid inspection criteria")
        if not 0 < self.distance_range[0] < self.distance_range[1]: raise ValueError("Invalid inspection distance range")
        if not 0.1 <= self.grid_resolution <= 1 or not 50 <= self.surface_samples <= 10000: raise ValueError("Invalid sampling settings")
        if len(self.initial_position)!=3 or len(self.initial_quaternion_xyzw)!=4: raise ValueError("Invalid starting pose")
        if not all(math.isfinite(v) for v in self.initial_position+self.initial_quaternion_xyzw): raise ValueError("Non-finite starting pose")
        if abs(sum(v*v for v in self.initial_quaternion_xyzw)-1)>1e-3: raise ValueError("Starting quaternion must be unit length")
        if self.target_bounds is not None:
            if len(self.target_bounds)!=2 or any(len(v)!=3 for v in self.target_bounds): raise ValueError("Invalid target bounds")
            if any(a>=b for a,b in zip(*self.target_bounds)): raise ValueError("Target bounds must have positive extent")

    @property
    def scene_path(self):
        p=Path(self.scene).expanduser()
        return p if p.is_absolute() else ROOT/p

    def dictionary(self): return asdict(self)
