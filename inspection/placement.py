"""Human-readable place of a damage: room, surface and height ("Bath · north wall · 1.4 m above floor").

Map conventions (set per map): `up_axis` ("z" or "y"), `north` = direction of north in the floor
plane (map coordinates), `floor_z` = floor height, `rooms` = [{"name", "polygon": [[x, y], ...],
"floor_z"?}] in floor-plane coordinates (drawn on the floor plan once per unit).
"""

import numpy as np

COMPASS = ["north", "east", "south", "west"]


def to_zup(p, up_axis: str = "z") -> np.ndarray:
    """Map coordinates -> z-up coordinates (x, y in the floor plane, z = height)."""
    p = np.asarray(p, dtype=np.float64)
    if up_axis == "z":
        return p
    if up_axis == "y":
        return np.array([p[..., 0], -p[..., 2], p[..., 1]]).T if p.ndim > 1 else np.array([p[0], -p[2], p[1]])
    raise ValueError(f"unsupported up_axis {up_axis!r}")


def point_in_polygon(x: float, y: float, poly: list) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def _dist_to_polygon(x: float, y: float, poly: list) -> float:
    best = np.inf
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        d = np.array([x1 - x0, y1 - y0], dtype=float)
        t = 0.0 if not d.any() else np.clip(((x - x0) * d[0] + (y - y0) * d[1]) / (d @ d), 0, 1)
        best = min(best, float(np.hypot(x - (x0 + t * d[0]), y - (y0 + t * d[1]))))
    return best


def room_at(xy, rooms: list[dict], tolerance_m: float = 0.3) -> dict | None:
    """Room containing the point; damage on a wall lies on the room boundary, so otherwise the
    nearest room within `tolerance_m`."""
    valid = [r for r in rooms or [] if len(r.get("polygon", [])) >= 3]
    for r in valid:
        if point_in_polygon(xy[0], xy[1], r["polygon"]):
            return r
    near = [(d, r) for r in valid if (d := _dist_to_polygon(xy[0], xy[1], r["polygon"])) <= tolerance_m]
    return min(near, key=lambda x: x[0])[1] if near else None


def surface(normal, north=(0.0, 1.0), up_axis: str = "z") -> str:
    """'floor' / 'ceiling' / '<compass> wall'. The normal points into the room (towards the camera),
    so a wall whose normal points south is the north wall."""
    n = to_zup(normal, up_axis)
    if np.linalg.norm(n) == 0:
        return "unknown"
    n = n / np.linalg.norm(n)
    if n[2] > 0.7:
        return "floor"
    if n[2] < -0.7:
        return "ceiling"
    side = -n[:2]                                  # direction from the room towards the wall
    nv = np.asarray(north, dtype=np.float64)
    nv = nv / np.linalg.norm(nv)
    east = np.array([nv[1], -nv[0]])               # north rotated clockwise (seen from above)
    ang = np.degrees(np.arctan2(side @ east, side @ nv)) % 360
    return f"{COMPASS[int(((ang + 45) % 360) // 90)]} wall"


def place(position, normal, *, up_axis: str = "z", north=(0.0, 1.0), floor_z: float | None = None,
          rooms: list[dict] | None = None) -> dict:
    p = to_zup(position, up_axis)
    room = room_at(p[:2], rooms or [])
    fz = room["floor_z"] if room and room.get("floor_z") is not None else floor_z
    return {
        "room": room["name"] if room else None,
        "surface": surface(normal, north, up_axis) if normal is not None and len(normal) == 3 else None,
        "height_m": round(float(p[2] - fz), 2) if fz is not None else None,
        "floor_xy": [round(float(p[0]), 3), round(float(p[1]), 3)],
    }


def describe(pl: dict) -> str:
    parts = [pl.get("room"), pl.get("surface")]
    return " · ".join(x for x in parts if x) or "unknown place"
