"""Detect a visible roof ridge line in the satellite image, as a refinement of
the footprint-edge azimuth heuristic.

This is fundamentally different from the rest of the app's geometry: it's
computer vision on a satellite/orthophoto tile, not a deterministic
calculation on OSM polygon data. Detection is deliberately conservative: a
candidate line only counts as a ridge if it's long, roughly central to the
footprint, and clearly distinct from the footprint's own boundary (otherwise
Canny/Hough will just re-detect the building outline as a "ridge"). When
nothing meets that bar, `detect_ridge_line` returns None and the caller
should keep using the footprint-edge default.

A real ridge and a coincidental same-bearing edge outside the building turn
out to look nearly identical by pure geometry (span, segment count, how far a
Hough-fitted endpoint overshoots the footprint) -- verified directly on a real
false positive and a real true positive that had almost the same numbers on
every geometric measure tried, including the false positive scoring *higher*
on span. The signal that did separate them: a real ridge usually splits the
roof into two differently lit/colored slopes, so the mean color on either
side of the candidate line differs noticeably; a coincidental edge that isn't
actually a roof feature doesn't. `_color_asymmetry` is that check, and is now
a required gate for every candidate, not just a fallback for uncorroborated
ones -- geometric corroboration alone was shown not to be trustworthy.
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
# A basic sanity check, not a precise discriminator: a Hough-fitted line's
# endpoint routinely overshoots the true footprint boundary by 15-20% of the
# bbox diagonal even for a genuine ridge (anti-aliasing, the OSM polygon not
# perfectly tracing the roof edge, eave overhang) -- a small fixed pixel
# margin rejected a confirmed-correct ridge in testing. This only guards
# against a candidate that's almost entirely outside the footprint; real
# corroboration is `_color_asymmetry` below.
_ENDPOINT_OUTSIDE_MARGIN_FRACTION = 0.25
# A candidate within this many pixels of the footprint boundary, running
# near-parallel to that boundary edge, is a re-detection of the building
# outline, not a ridge.
_BOUNDARY_PROXIMITY_PX = 6.0
_BOUNDARY_PARALLEL_TOLERANCE_DEG = 10.0
# Candidates within this bearing tolerance (mod 180) are grouped together
# when picking the single most-supported line.
_GROUP_TOLERANCE_DEG = 12.0
# Sample points in bands on each side of a candidate line (within the
# footprint) and compare mean RGB. Calibrated on two real examples: a
# confirmed true ridge measured ~45, a confirmed false positive measured ~11
# -- 20 sits with margin above the false positive and below the true one, but
# this is only two data points, so revisit if it misfires on new examples.
_COLOR_ASYMMETRY_BAND_PX = 6.0
_COLOR_ASYMMETRY_SAMPLES = 15
_MIN_COLOR_ASYMMETRY = 20.0


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

        margin_px = bbox_diagonal * _ENDPOINT_OUTSIDE_MARGIN_FRACTION
        if not _inside_with_margin(p1, ring_px, margin_px) or not _inside_with_margin(p2, ring_px, margin_px):
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
    return _pick_dominant_line(candidates, bbox_diagonal, image, ring_px)


def _inside_with_margin(pt: tuple[float, float], ring_px: list[tuple[float, float]], margin_px: float) -> bool:
    if point_in_polygon(pt, ring_px):
        return True
    return point_to_ring_distance(pt, ring_px) <= margin_px


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


def _color_asymmetry(
    image: Image.Image,
    p1: tuple[float, float],
    p2: tuple[float, float],
    ring_px: list[tuple[float, float]],
) -> float | None:
    """Mean-color difference between the two sides of the line (p1, p2),
    sampled only at points inside the footprint. A real ridge splits the roof
    into two differently lit/colored slopes; a coincidental edge that isn't
    actually a roof feature usually doesn't. Returns None if there aren't
    enough valid samples on both sides to compare (e.g. the line runs too
    close to the footprint boundary for a full band on one side).
    """
    pixels = np.asarray(image.convert("RGB"), dtype=np.float64)
    height, width = pixels.shape[:2]
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    ux, uy = dx / length, dy / length
    perp_x, perp_y = -uy, ux

    sides: tuple[list, list] = ([], [])
    for i in range(_COLOR_ASYMMETRY_SAMPLES + 1):
        t = i / _COLOR_ASYMMETRY_SAMPLES
        cx, cy = p1[0] + t * dx, p1[1] + t * dy
        for side, bucket in ((1, sides[0]), (-1, sides[1])):
            sx = cx + perp_x * _COLOR_ASYMMETRY_BAND_PX * side
            sy = cy + perp_y * _COLOR_ASYMMETRY_BAND_PX * side
            if 0 <= sx < width and 0 <= sy < height and point_in_polygon((sx, sy), ring_px):
                bucket.append(pixels[int(sy), int(sx)])

    left, right = sides
    if len(left) < 3 or len(right) < 3:
        return None
    return float(np.linalg.norm(np.mean(left, axis=0) - np.mean(right, axis=0)))


def _group_by_bearing(candidates: list[RidgeLine]) -> list[list[RidgeLine]]:
    remaining = sorted(candidates, key=lambda c: c.length_px, reverse=True)
    used = [False] * len(remaining)
    groups: list[list[RidgeLine]] = []

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
        groups.append(group)
    return groups


def _pick_dominant_line(
    candidates: list[RidgeLine],
    bbox_diagonal: float,
    image: Image.Image,
    ring_px: list[tuple[float, float]],
) -> RidgeLine | None:
    # Group by similar bearing (mod 180) -- segment count and total span were
    # the original corroboration signals for picking *among* groups, but
    # testing showed they don't actually separate a real ridge from a
    # coincidental same-bearing edge outside the building (a confirmed false
    # positive scored *higher* on span than a confirmed true positive, and
    # two spurious edges can agree in bearing just as easily as two real
    # ones). So: try groups in order of total supporting length, and accept
    # the first one whose representative line shows a real color difference
    # between its two sides (see module docstring) -- the strongest-looking
    # group by raw geometry isn't necessarily the real ridge.
    groups = sorted(_group_by_bearing(candidates), key=lambda g: sum(c.length_px for c in g), reverse=True)

    for group in groups:
        best = max(group, key=lambda c: c.length_px)
        asymmetry = _color_asymmetry(image, best.start_px, best.end_px, ring_px)
        if asymmetry is not None and asymmetry >= _MIN_COLOR_ASYMMETRY:
            return best
    return None
