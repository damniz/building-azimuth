"""Shared planar-geometry primitives for building footprint analysis.

Building footprints are small enough (tens of meters) that a local flat-earth
projection around the footprint's own centroid introduces negligible error,
avoiding a dependency on pyproj/shapely. All functions here operate on
"rings": ordered lists of vertices with no duplicated closing vertex -- edges
wrap around from the last vertex back to the first.
"""

from __future__ import annotations

import math

Point = tuple[float, float]

# Meters per degree of latitude, derived from the WGS84 equatorial radius
# (pi/180 * 6378137). Used for BOTH axes (longitude scaled by cos(latitude))
# so the local projection stays conformal -- mixing an equatorial constant on
# one axis with a latitude-corrected constant on the other would rotate
# computed bearings.
_METERS_PER_DEGREE = 111_319.5


def project_to_local_meters(points_latlon: list[Point], origin: Point) -> list[Point]:
    """Project (lat, lon) points to local planar meters around `origin` (lat, lon)."""
    lat0, lon0 = origin
    cos_lat0 = math.cos(math.radians(lat0))
    return [
        (
            (lon - lon0) * _METERS_PER_DEGREE * cos_lat0,
            (lat - lat0) * _METERS_PER_DEGREE,
        )
        for lat, lon in points_latlon
    ]


def point_in_polygon(pt_xy: Point, ring_xy: list[Point]) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = pt_xy
    inside = False
    n = len(ring_xy)
    for i in range(n):
        x1, y1 = ring_xy[i]
        x2, y2 = ring_xy[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_intersect = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_intersect:
                inside = not inside
    return inside


def polygon_area(ring_xy: list[Point]) -> float:
    """Shoelace formula; returns a non-negative area in square meters."""
    n = len(ring_xy)
    total = 0.0
    for i in range(n):
        x1, y1 = ring_xy[i]
        x2, y2 = ring_xy[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polygon_centroid(ring_xy: list[Point]) -> Point:
    """Area-weighted centroid; falls back to the vertex average for degenerate rings."""
    n = len(ring_xy)
    area_sum = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x1, y1 = ring_xy[i]
        x2, y2 = ring_xy[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        area_sum += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area_sum) < 1e-9:
        xs = [p[0] for p in ring_xy]
        ys = [p[1] for p in ring_xy]
        return sum(xs) / n, sum(ys) / n
    area = area_sum / 2.0
    return cx / (6 * area), cy / (6 * area)


def _point_to_segment_distance(pt: Point, a: Point, b: Point) -> float:
    px, py = pt
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    closest_x = ax + t * dx
    closest_y = ay + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def point_to_ring_distance(pt_xy: Point, ring_xy: list[Point]) -> float:
    """Minimum distance from a point to any edge of the ring (its boundary)."""
    n = len(ring_xy)
    return min(
        _point_to_segment_distance(pt_xy, ring_xy[i], ring_xy[(i + 1) % n])
        for i in range(n)
    )
