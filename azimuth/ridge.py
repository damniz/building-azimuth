"""Detect a visible roof ridge line in the satellite image, as a refinement of
the footprint-edge azimuth heuristic.

This is fundamentally different from the rest of the app's geometry: it's
computer vision on a low-resolution satellite tile (roughly 0.3-1m/pixel at
the zoom levels imagery.py fetches), not a deterministic calculation on OSM
polygon data. A typical small residential roof is only a few dozen pixels
wide at this resolution, so detection is deliberately conservative: a
candidate line only counts as a ridge if it's long, roughly central to the
footprint, and clearly distinct from the footprint's own boundary (otherwise
Canny/Hough will just re-detect the building outline as a "ridge"). When
nothing meets that bar, `detect_ridge_line` returns None and the caller
should keep using the footprint-edge default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from .geometry import point_in_polygon, point_to_ring_distance, polygon_centroid
from .orientation import EdgeBearing

# A candidate must span at least this fraction of the footprint's bounding-box
# diagonal to be a plausible ridge (rules out dormer/window/chimney edges).
_MIN_SPAN_FRACTION = 0.45
# A candidate's midpoint must be within this fraction of the bbox diagonal
# from the footprint centroid -- a ridge runs roughly through the middle.
_MAX_CENTROID_DISTANCE_FRACTION = 0.25
# Both endpoints must be inside the footprint (plus this small pixel margin,
# for boundary imprecision) -- not just the midpoint. A line whose midpoint
# happens to fall inside but which exits through a corner is very likely a
# color-boundary edge with the surroundings (a driveway, a neighbor's roof,
# a shadow), not a ridge spanning this roof.
_ENDPOINT_OUTSIDE_MARGIN_PX = 2.0
# A candidate within this many pixels of the footprint boundary, running
# near-parallel to that boundary edge, is a re-detection of the building
# outline, not a ridge.
_BOUNDARY_PROXIMITY_PX = 6.0
_BOUNDARY_PARALLEL_TOLERANCE_DEG = 10.0
# Candidates within this bearing tolerance (mod 180) are grouped together
# when picking the single most-supported line.
_GROUP_TOLERANCE_DEG = 12.0
# A single, unconfirmed line is a weak signal at the resolution this imagery
# is fetched at -- require either multiple independent segments agreeing, or
# one segment spanning almost the entire footprint, before trusting it.
_MIN_CORROBORATING_CANDIDATES = 2
_SOLO_CANDIDATE_MIN_SPAN_FRACTION = 0.8


@dataclass
class RidgeLine:
    start_px: tuple[float, float]
    end_px: tuple[float, float]
    length_px: float


def detect_ridge_line(image: Image.Image, ring_px: list[tuple[float, float]]) -> RidgeLine | None:
    """Look for a straight line spanning the footprint that would indicate a
    roof ridge splitting it into two slopes. `ring_px` is the footprint
    polygon already projected into `image`'s pixel space (see
    `imagery.project_ring_to_pixels`). Returns None if nothing sufficiently
    confident is found.
    """
    xs = [p[0] for p in ring_px]
    ys = [p[1] for p in ring_px]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    bbox_diagonal = math.hypot(max_x - min_x, max_y - min_y)
    if bbox_diagonal <= 0:
        return None

    pad = max(2.0, bbox_diagonal * 0.05)
    left = max(0, int(min_x - pad))
    top = max(0, int(min_y - pad))
    right = min(image.width, int(max_x + pad))
    bottom = min(image.height, int(max_y + pad))
    if right - left < 6 or bottom - top < 6:
        return None

    gray = np.array(image.crop((left, top, right, bottom)).convert("L"))
    edges = cv2.Canny(gray, 50, 150)

    min_line_length = max(8, int(bbox_diagonal * _MIN_SPAN_FRACTION))
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=20,
        minLineLength=min_line_length,
        maxLineGap=max(2, int(bbox_diagonal * 0.08)),
    )
    if lines is None:
        return None

    centroid = polygon_centroid(ring_px)
    candidates: list[RidgeLine] = []

    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        p1 = (float(x1 + left), float(y1 + top))
        p2 = (float(x2 + left), float(y2 + top))
        length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if length < min_line_length:
            continue

        if not _inside_with_margin(p1, ring_px) or not _inside_with_margin(p2, ring_px):
            continue

        midpoint = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
        centroid_distance = math.hypot(midpoint[0] - centroid[0], midpoint[1] - centroid[1])
        if centroid_distance > bbox_diagonal * _MAX_CENTROID_DISTANCE_FRACTION:
            continue

        bearing = _pixel_bearing(p1, p2)
        if _hugs_boundary(p1, p2, bearing, ring_px):
            continue

        candidates.append(RidgeLine(start_px=p1, end_px=p2, length_px=length))

    if not candidates:
        return None
    return _pick_dominant_line(candidates, bbox_diagonal)


def _inside_with_margin(pt: tuple[float, float], ring_px: list[tuple[float, float]]) -> bool:
    if point_in_polygon(pt, ring_px):
        return True
    return point_to_ring_distance(pt, ring_px) <= _ENDPOINT_OUTSIDE_MARGIN_PX


def ridge_azimuth(line: RidgeLine, meters_per_pixel: float) -> EdgeBearing:
    """Convert a detected ridge line's pixel-space endpoints into an
    EdgeBearing, using the same [0, 180) line convention as the footprint-edge
    heuristic (a ridge is a line, not a direction).
    """
    bearing = _pixel_bearing(line.start_px, line.end_px)
    return EdgeBearing(bearing_deg=bearing, length_m=line.length_px * meters_per_pixel)


def _pixel_bearing(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    return math.degrees(math.atan2(dx, -dy)) % 180.0


def _point_to_segment_distance_px(
    pt: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    px, py = pt
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _hugs_boundary(
    p1: tuple[float, float], p2: tuple[float, float], bearing_deg: float, ring_px: list[tuple[float, float]]
) -> bool:
    n = len(ring_px)
    for i in range(n):
        a, b = ring_px[i], ring_px[(i + 1) % n]
        edge_bearing = _pixel_bearing(a, b)
        diff = abs(bearing_deg - edge_bearing) % 180.0
        diff = min(diff, 180.0 - diff)
        if diff > _BOUNDARY_PARALLEL_TOLERANCE_DEG:
            continue
        if (
            _point_to_segment_distance_px(p1, a, b) < _BOUNDARY_PROXIMITY_PX
            and _point_to_segment_distance_px(p2, a, b) < _BOUNDARY_PROXIMITY_PX
        ):
            return True
    return False


def _pick_dominant_line(candidates: list[RidgeLine], bbox_diagonal: float) -> RidgeLine | None:
    # Group by similar bearing (mod 180), sum length per group, then return
    # the single longest segment from the best-supported group -- but only
    # if that group is actually corroborated: either multiple independent
    # segments agree, or one segment alone spans almost the whole footprint.
    # A single, middling-length line is exactly the failure mode observed in
    # testing (a stray color-boundary edge coinciding with the containment
    # checks), so it's not enough on its own.
    remaining = sorted(candidates, key=lambda c: c.length_px, reverse=True)
    used = [False] * len(remaining)
    best_group: list[RidgeLine] = []
    best_total = -1.0

    for i, seed in enumerate(remaining):
        if used[i]:
            continue
        seed_bearing = _pixel_bearing(seed.start_px, seed.end_px)
        group = [seed]
        used[i] = True
        for j in range(i + 1, len(remaining)):
            if used[j]:
                continue
            other_bearing = _pixel_bearing(remaining[j].start_px, remaining[j].end_px)
            diff = abs(other_bearing - seed_bearing) % 180.0
            diff = min(diff, 180.0 - diff)
            if diff <= _GROUP_TOLERANCE_DEG:
                group.append(remaining[j])
                used[j] = True
        total = sum(c.length_px for c in group)
        if total > best_total:
            best_total = total
            best_group = group

    best = max(best_group, key=lambda c: c.length_px)
    if len(best_group) < _MIN_CORROBORATING_CANDIDATES and best.length_px < bbox_diagonal * _SOLO_CANDIDATE_MIN_SPAN_FRACTION:
        return None
    return best
