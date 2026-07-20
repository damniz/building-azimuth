"""Footprint polygon -> roof azimuth.

The roof ridge direction is approximated by the orientation of the
building's footprint edges (a common heuristic: for typical gable/hip roofs
the ridge runs parallel to the building's longest wall). Since a footprint
edge is a line, not a vector, bearings are normalized to [0, 180) rather than
a full compass direction -- the footprint alone can't distinguish "45 degrees"
from "225 degrees".

`group_azimuths` clusters all edges by similar bearing and reports each
group's share of the perimeter. The MVP only surfaces the single dominant
azimuth, but this grouping is the natural foundation for the planned
multiple-azimuths-with-roof-fraction feature for complex buildings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Point

# Very short edges (chimneys, bay windows, digitization noise) have
# essentially random bearings and would pollute grouping if left in.
_MIN_EDGE_LENGTH_M = 1.5

_DEFAULT_GROUP_TOLERANCE_DEG = 15.0


@dataclass
class EdgeBearing:
    bearing_deg: float  # normalized to [0, 180)
    length_m: float


@dataclass
class AzimuthGroup:
    bearing_deg: float  # representative bearing of the group, [0, 180)
    total_length_m: float
    length_fraction: float  # total_length_m / perimeter


def edge_bearings(ring_xy: list[Point], min_length_m: float = _MIN_EDGE_LENGTH_M) -> list[EdgeBearing]:
    n = len(ring_xy)
    all_edges = []
    for i in range(n):
        ax, ay = ring_xy[i]
        bx, by = ring_xy[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        bearing = math.degrees(math.atan2(dx, dy)) % 360.0
        all_edges.append(EdgeBearing(bearing_deg=bearing % 180.0, length_m=length))

    filtered = [e for e in all_edges if e.length_m >= min_length_m]
    return filtered if filtered else all_edges


def dominant_azimuth(edges: list[EdgeBearing]) -> EdgeBearing:
    if not edges:
        raise ValueError("Cannot compute a dominant azimuth from an empty edge list.")
    return max(edges, key=lambda e: e.length_m)


def apply_roof_orientation_hint(
    primary: EdgeBearing, roof_orientation: str | None
) -> tuple[EdgeBearing, str | None]:
    """Override the footprint-edge heuristic with an explicit, user-supplied
    choice. `along` confirms the default assumption (ridge parallel to the
    longer edge, i.e. what `dominant_azimuth` already returns) -- no change.
    `across` means the ridge runs parallel to the *shorter* edge -- rotate by
    90 degrees. `flat` means the building is known to have no ridge -- the
    standard footprint-edge heuristic still stands (it's the best available
    signal for a reference orientation), this just marks that ridge detection
    shouldn't be attempted for it (see pipeline.py).

    Returns the (possibly corrected) bearing, plus the value that was
    actually applied (None if absent or an unrecognized value, in which case
    the longest-edge default stands).
    """
    if roof_orientation == "across":
        rotated = EdgeBearing(bearing_deg=(primary.bearing_deg + 90.0) % 180.0, length_m=primary.length_m)
        return rotated, "across"
    if roof_orientation in ("along", "flat"):
        return primary, roof_orientation
    return primary, None


def _circular_diff_deg(a: float, b: float, modulus: float = 180.0) -> float:
    d = abs(a - b) % modulus
    return min(d, modulus - d)


def to_directional_azimuth(bearing_deg: float) -> float:
    """Convert a ridge/wall-line bearing ([0, 180), the convention used
    everywhere else in this module) into the full-circle azimuth this app
    reports to users and the API.

    A ridge line itself isn't what a solar panel faces -- the roof *slopes*
    do, and those run perpendicular to the ridge, one on each side. Rotate by
    90 degrees to get that perpendicular line, then resolve its two possible
    full-circle directions by picking whichever is closer to south (180
    degrees), the convention this app has settled on for solar-relevance.
    """
    perpendicular = (bearing_deg + 90.0) % 180.0
    candidate_a = perpendicular
    candidate_b = (perpendicular + 180.0) % 360.0
    if _circular_diff_deg(candidate_a, 180.0, 360.0) <= _circular_diff_deg(candidate_b, 180.0, 360.0):
        return candidate_a
    return candidate_b


def _circular_mean_deg(bearings_deg: list[float], weights: list[float]) -> float:
    # Double the angle to fold the mod-180 ambiguity into a full mod-360
    # circle, average as vectors, then halve -- avoids naive averaging
    # mis-handling bearings that straddle the 0/180 wraparound.
    x = sum(w * math.cos(math.radians(2 * b)) for b, w in zip(bearings_deg, weights))
    y = sum(w * math.sin(math.radians(2 * b)) for b, w in zip(bearings_deg, weights))
    if x == 0 and y == 0:
        return bearings_deg[0]
    return (math.degrees(math.atan2(y, x)) / 2.0) % 180.0


def group_azimuths(
    edges: list[EdgeBearing], tolerance_deg: float = _DEFAULT_GROUP_TOLERANCE_DEG
) -> list[AzimuthGroup]:
    perimeter = sum(e.length_m for e in edges)
    remaining = sorted(edges, key=lambda e: e.length_m, reverse=True)
    raw_groups: list[list[EdgeBearing]] = []

    while remaining:
        seed = remaining.pop(0)
        members = [seed]
        rest = []
        for e in remaining:
            if _circular_diff_deg(e.bearing_deg, seed.bearing_deg, 180.0) <= tolerance_deg:
                members.append(e)
            else:
                rest.append(e)
        remaining = rest
        raw_groups.append(members)

    groups = [
        AzimuthGroup(
            bearing_deg=_circular_mean_deg(
                [m.bearing_deg for m in members], [m.length_m for m in members]
            ),
            total_length_m=sum(m.length_m for m in members),
            length_fraction=(sum(m.length_m for m in members) / perimeter if perimeter > 0 else 0.0),
        )
        for members in raw_groups
    ]
    groups.sort(key=lambda g: g.total_length_m, reverse=True)
    return groups
