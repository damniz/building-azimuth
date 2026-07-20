"""Fetch and stitch satellite tiles into a single image around a building footprint.

Uses Esri World Imagery's keyless tile endpoint (works today for hobby/OSS
use, the same one countless Leaflet projects hit directly with no auth,
though it isn't formally guaranteed free -- attribution is shown in the UI as
cheap insurance). Tries the highest zoom first and falls back downward: z=20+
("Clarity") coverage is patchy and city-specific, but where it exists it's a
real resolution win -- worth attempting, since ridge-line detection (ridge.py)
is starved for pixels on typical small roofs. z=19 remains the practical
ceiling almost everywhere else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO

import mercantile
import numpy as np
import requests
from PIL import Image

TILE_SIZE = 256
ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ESRI_ATTRIBUTION = "Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community"

_METERS_PER_DEGREE = 111_319.5

# Esri serves "no imagery here yet" tiles (a flat gray "Map data not yet
# available" graphic) as an ordinary HTTP 200, not an error status -- so a
# status-code check alone silently accepts them as real imagery. Real
# satellite tiles have far more pixel variance than this placeholder
# (observed: real tiles std >= ~44, placeholder tiles std ~= 5); this
# threshold leaves a wide margin on both sides.
_PLACEHOLDER_STD_THRESHOLD = 15.0


class ImageryError(Exception):
    """Raised when satellite imagery cannot be fetched for a location."""


@dataclass
class StitchedImage:
    image: Image.Image
    zoom: int
    canvas_origin_px: tuple[float, float]  # pixel coords (at `zoom`) of this image's top-left corner


def lonlat_to_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Global slippy-map pixel coordinates for (lon, lat) at a given zoom."""
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n * TILE_SIZE
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * TILE_SIZE
    return x, y


def meters_per_pixel(lat: float, zoom: int) -> float:
    """Ground resolution of a Web Mercator tile at `lat`/`zoom` (standard formula)."""
    return (156543.03392 * math.cos(math.radians(lat))) / (2**zoom)


def project_ring_to_pixels(
    ring_latlon: list[tuple[float, float]], stitched: StitchedImage
) -> list[tuple[float, float]]:
    """Project a (lat, lon) ring into pixel coordinates within `stitched.image`."""
    ox, oy = stitched.canvas_origin_px
    return [
        (px - ox, py - oy)
        for px, py in (lonlat_to_pixel(lon, lat, stitched.zoom) for lat, lon in ring_latlon)
    ]


def _pad_bbox(
    bbox_latlon: tuple[float, float, float, float], pad_m: float
) -> tuple[float, float, float, float]:
    min_lat, min_lon, max_lat, max_lon = bbox_latlon
    mean_lat = (min_lat + max_lat) / 2.0
    dlat = pad_m / _METERS_PER_DEGREE
    dlon = pad_m / (_METERS_PER_DEGREE * math.cos(math.radians(mean_lat)))
    return (min_lat - dlat, min_lon - dlon, max_lat + dlat, max_lon + dlon)


def _is_placeholder_tile(image: Image.Image) -> bool:
    gray = np.array(image.convert("L"), dtype=np.float64)
    return gray.std() < _PLACEHOLDER_STD_THRESHOLD


def _fetch_tile(z: int, x: int, y: int, user_agent: str, timeout: float = 10.0) -> Image.Image:
    url = ESRI_TILE_URL.format(z=z, y=y, x=x)
    response = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
    if response.status_code != 200:
        raise ImageryError(f"Tile {z}/{x}/{y} unavailable (HTTP {response.status_code}).")
    image = Image.open(BytesIO(response.content)).convert("RGB")
    if _is_placeholder_tile(image):
        raise ImageryError(f"Tile {z}/{x}/{y} has no imagery yet (placeholder tile).")
    return image


def fetch_stitched_image(
    bbox_latlon: tuple[float, float, float, float],
    pad_m: float,
    zoom_candidates: tuple[int, ...] = (21, 20, 19, 18, 17),
    user_agent: str = "building-azimuth/0.1",
) -> StitchedImage:
    """Fetch and stitch tiles covering `bbox_latlon` (min_lat, min_lon, max_lat, max_lon)."""
    padded = _pad_bbox(bbox_latlon, pad_m)
    min_lat, min_lon, max_lat, max_lon = padded

    last_error: Exception | None = None
    for zoom in zoom_candidates:
        try:
            tiles = list(mercantile.tiles(min_lon, min_lat, max_lon, max_lat, [zoom]))
            if not tiles:
                continue
            min_tx = min(t.x for t in tiles)
            max_tx = max(t.x for t in tiles)
            min_ty = min(t.y for t in tiles)
            max_ty = max(t.y for t in tiles)

            canvas = Image.new(
                "RGB",
                ((max_tx - min_tx + 1) * TILE_SIZE, (max_ty - min_ty + 1) * TILE_SIZE),
            )
            for tile in tiles:
                tile_img = _fetch_tile(zoom, tile.x, tile.y, user_agent)
                canvas.paste(tile_img, ((tile.x - min_tx) * TILE_SIZE, (tile.y - min_ty) * TILE_SIZE))

            canvas_origin_px = (min_tx * TILE_SIZE, min_ty * TILE_SIZE)
            top_left_px = lonlat_to_pixel(min_lon, max_lat, zoom)
            bottom_right_px = lonlat_to_pixel(max_lon, min_lat, zoom)
            crop_box = (
                int(top_left_px[0] - canvas_origin_px[0]),
                int(top_left_px[1] - canvas_origin_px[1]),
                int(bottom_right_px[0] - canvas_origin_px[0]),
                int(bottom_right_px[1] - canvas_origin_px[1]),
            )
            cropped = canvas.crop(crop_box)
            return StitchedImage(image=cropped, zoom=zoom, canvas_origin_px=top_left_px)
        except (ImageryError, requests.RequestException) as exc:
            last_error = exc
            continue

    raise ImageryError(f"Could not fetch satellite imagery at any zoom level: {last_error}")
