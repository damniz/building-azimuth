"""Draw the building footprint outline and azimuth line onto a stitched image.

The azimuth is drawn as a single line through the footprint's centroid,
extending in both the bearing direction and bearing+180 -- a single
arrowhead would falsely imply a forward/backward direction that a footprint
polygon alone can't support (it's a line, not a vector). Small perpendicular
end ticks make it read as a measured line rather than an arrow.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from . import geometry
from .imagery import StitchedImage, lonlat_to_pixel
from .orientation import EdgeBearing

FOOTPRINT_COLOR = (0, 220, 255)
AZIMUTH_COLOR = (255, 60, 60)
FOOTPRINT_WIDTH = 3
AZIMUTH_WIDTH = 4
_END_TICK_PX = 10


def _project_ring_to_pixels(
    ring_latlon: list[tuple[float, float]], stitched: StitchedImage
) -> list[tuple[float, float]]:
    ox, oy = stitched.canvas_origin_px
    return [
        (px - ox, py - oy)
        for px, py in (lonlat_to_pixel(lon, lat, stitched.zoom) for lat, lon in ring_latlon)
    ]


def draw_footprint_and_azimuth(
    stitched: StitchedImage,
    ring_latlon: list[tuple[float, float]],
    primary: EdgeBearing,
) -> Image.Image:
    image = stitched.image.copy()
    draw = ImageDraw.Draw(image)

    ring_px = _project_ring_to_pixels(ring_latlon, stitched)
    draw.polygon(ring_px, outline=FOOTPRINT_COLOR, width=FOOTPRINT_WIDTH)

    xs = [p[0] for p in ring_px]
    ys = [p[1] for p in ring_px]
    bbox_diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    half_length = max(bbox_diagonal * 0.65, 20.0)

    cx, cy = geometry.polygon_centroid(ring_px)
    bearing_rad = math.radians(primary.bearing_deg)
    ux, uy = math.sin(bearing_rad), -math.cos(bearing_rad)

    x1, y1 = cx - ux * half_length, cy - uy * half_length
    x2, y2 = cx + ux * half_length, cy + uy * half_length
    draw.line([(x1, y1), (x2, y2)], fill=AZIMUTH_COLOR, width=AZIMUTH_WIDTH)

    perp_x, perp_y = -uy, ux
    for x, y in ((x1, y1), (x2, y2)):
        draw.line(
            [
                (x - perp_x * _END_TICK_PX, y - perp_y * _END_TICK_PX),
                (x + perp_x * _END_TICK_PX, y + perp_y * _END_TICK_PX),
            ],
            fill=AZIMUTH_COLOR,
            width=AZIMUTH_WIDTH,
        )

    return image
