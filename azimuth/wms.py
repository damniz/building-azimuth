"""Fetch high-resolution orthophoto imagery from Belgium's national WMS
service (NGI), for meaningfully better ridge-detection resolution than Esri
in Belgium: Flanders' orthophotos are ~15cm/pixel and Wallonia's ~25cm/pixel,
both consistently available for the whole region (unlike Esri's patchy
z20/21 "Clarity" coverage, which turned out to be placeholder tiles at both
Belgian addresses tested).

Only covers Belgium. `pipeline.py` tries this first for in-region addresses
and falls back to `imagery.py`'s Esri source on any failure (including
addresses outside Belgium, where this is skipped entirely).
"""

from __future__ import annotations

import math
from io import BytesIO

import numpy as np
import requests
from PIL import Image

from .imagery import StitchedImage, lonlat_to_pixel, pad_bbox

NGI_WMS_URL = "https://wms.ngi.be/inspire/ortho/service"
NGI_WMS_LAYER = "orthoimage_coverage"  # most recent available campaign (2023/2024)
NGI_ATTRIBUTION = "Imagery: NGI (Nationaal Geografisch Instituut / Institut Géographique National)"

_WEB_MERCATOR_RADIUS = 6_378_137.0  # WGS84 semi-major axis, meters

# A zoom "level" in the same sense imagery.py uses it -- picked to sit
# comfortably above both regions' native resolution (~0.1-0.2 m/pixel across
# Belgium's latitudes) without requesting more detail than the source has.
_DEFAULT_ZOOM = 20
_MAX_DIMENSION_PX = 2048  # guard against pathologically large requests

# Belgium's bounding box (lon_min, lat_min, lon_max, lat_max), with margin --
# used to skip this source outright for addresses clearly outside its coverage.
_BELGIUM_BBOX = (2.3, 49.3, 6.5, 51.6)

# Same placeholder/no-data heuristic as imagery.py's Esri tiles: real
# orthophoto content has far more pixel variance than a blank/error image.
_BLANK_STD_THRESHOLD = 15.0


class WmsError(Exception):
    """Raised when the Belgian WMS imagery cannot be fetched."""


def is_in_belgium(lat: float, lon: float) -> bool:
    lon_min, lat_min, lon_max, lat_max = _BELGIUM_BBOX
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _lonlat_to_web_mercator(lon: float, lat: float) -> tuple[float, float]:
    x = math.radians(lon) * _WEB_MERCATOR_RADIUS
    y = math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * _WEB_MERCATOR_RADIUS
    return x, y


def _looks_blank(image: Image.Image) -> bool:
    gray = np.array(image.convert("L"), dtype=np.float64)
    return gray.std() < _BLANK_STD_THRESHOLD


def fetch_wms_image(
    bbox_latlon: tuple[float, float, float, float],
    pad_m: float,
    zoom: int = _DEFAULT_ZOOM,
    user_agent: str = "building-azimuth/0.1",
    timeout: float = 15.0,
) -> StitchedImage:
    """Fetch NGI's Belgian orthophoto covering `bbox_latlon` (min_lat, min_lon, max_lat, max_lon).

    Returns a `StitchedImage` with the same `zoom`/`canvas_origin_px`
    conventions as imagery.py's tile-based fetch, so render.py and ridge.py
    work with either source unmodified: pixel positions are computed from
    `lonlat_to_pixel` at `zoom`, and the image is requested at exactly the
    width/height that implies.
    """
    min_lat, min_lon, max_lat, max_lon = pad_bbox(bbox_latlon, pad_m)

    top_left_px = lonlat_to_pixel(min_lon, max_lat, zoom)
    bottom_right_px = lonlat_to_pixel(max_lon, min_lat, zoom)
    width_px = max(1, round(bottom_right_px[0] - top_left_px[0]))
    height_px = max(1, round(bottom_right_px[1] - top_left_px[1]))
    if width_px > _MAX_DIMENSION_PX or height_px > _MAX_DIMENSION_PX:
        raise WmsError(f"Requested image too large ({width_px}x{height_px}) for zoom {zoom}.")

    x_min, y_min = _lonlat_to_web_mercator(min_lon, min_lat)
    x_max, y_max = _lonlat_to_web_mercator(max_lon, max_lat)

    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": NGI_WMS_LAYER,
        "STYLES": "",
        "CRS": "EPSG:3857",
        "BBOX": f"{x_min},{y_min},{x_max},{y_max}",
        "WIDTH": width_px,
        "HEIGHT": height_px,
        "FORMAT": "image/png",
    }

    try:
        response = requests.get(
            NGI_WMS_URL, params=params, headers={"User-Agent": user_agent}, timeout=timeout
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type:
            raise WmsError(f"WMS returned non-image content ({content_type}).")
        image = Image.open(BytesIO(response.content)).convert("RGB")
    except requests.RequestException as exc:
        raise WmsError(f"Could not reach the Belgian WMS imagery service: {exc}") from exc

    if _looks_blank(image):
        raise WmsError("WMS returned a blank/no-data image for this location.")

    return StitchedImage(image=image, zoom=zoom, canvas_origin_px=top_left_px)
