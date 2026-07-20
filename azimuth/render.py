"""Draw the building footprint outline and a single azimuth line onto a stitched image."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from . import geometry
from .imagery import StitchedImage, project_ring_to_pixels
from .orientation import EdgeBearing

FOOTPRINT_COLOR = (0, 220, 255)
AZIMUTH_COLOR = (255, 60, 60)
FOOTPRINT_WIDTH = 3
AZIMUTH_WIDTH = 4


def draw_footprint_and_azimuth(
    stitched: StitchedImage,
    ring_latlon: list[tuple[float, float]],
    primary: EdgeBearing,
) -> Image.Image:
    image = stitched.image.copy()
    draw = ImageDraw.Draw(image)

    ring_px = project_ring_to_pixels(ring_latlon, stitched)
    draw.polygon(ring_px, outline=FOOTPRINT_COLOR, width=FOOTPRINT_WIDTH)

    xs = [p[0] for p in ring_px]
    ys = [p[1] for p in ring_px]
    bbox_diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    half_length = max(bbox_diagonal * 0.65, 20.0)

    cx, cy = geometry.polygon_centroid(ring_px)
    bearing_rad = math.radians(primary.bearing_deg)
    ux, uy = math.sin(bearing_rad), -math.cos(bearing_rad)

    tail = (cx - ux * half_length, cy - uy * half_length)
    tip = (cx + ux * half_length, cy + uy * half_length)
    draw.line([tail, tip], fill=AZIMUTH_COLOR, width=AZIMUTH_WIDTH)

    return image
