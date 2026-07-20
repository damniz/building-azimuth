"""Orchestrates geocode -> footprint -> orientation -> (optional) imagery.

Two cached entry points share one cached "locate" step, so JSON-only API
callers (`compute_orientation`) skip the imagery fetch -- the slowest, most
failure-prone part of the pipeline -- while the Streamlit UI and the image
API endpoint (`compute_building_azimuth`) get the full result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import streamlit as st
from PIL import Image

from . import footprint as footprint_mod
from . import geocode as geocode_mod
from . import geometry
from . import imagery as imagery_mod
from . import orientation as orientation_mod
from . import render as render_mod
from . import ridge as ridge_mod
from . import wms as wms_mod

USER_AGENT = "building-azimuth/0.1 (+contact: damien.nizery@lastrolabe.eu)"
CACHE_TTL_SECONDS = 86400


@dataclass
class AzimuthResult:
    address: str
    display_name: str
    lat: float
    lon: float
    primary: orientation_mod.EdgeBearing
    groups: list[orientation_mod.AzimuthGroup]
    roof_orientation_hint: str | None  # "along"/"across"/"flat" if the user overrode it, else None
    ridge_detected: bool  # True if a visible roof ridge line was detected in the image
    image: Image.Image | None
    image_attribution: str | None


@dataclass
class _LocatedBuilding:
    display_name: str
    lat: float
    lon: float
    ring_latlon: list[tuple[float, float]]
    primary: orientation_mod.EdgeBearing
    groups: list[orientation_mod.AzimuthGroup]
    roof_orientation_hint: str | None


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _locate_building(address: str, manual_orientation: str | None = None) -> _LocatedBuilding:
    geocoded = geocode_mod.geocode_address(address, user_agent=USER_AGENT)
    building = footprint_mod.find_building(geocoded.lat, geocoded.lon, user_agent=USER_AGENT)

    ring_xy = geometry.project_to_local_meters(building.ring_latlon, origin=(geocoded.lat, geocoded.lon))
    edges = orientation_mod.edge_bearings(ring_xy)
    primary = orientation_mod.dominant_azimuth(edges)
    groups = orientation_mod.group_azimuths(edges)

    # Default is always the longest-edge heuristic: OSM's roof:orientation tag turned
    # out not to be reliable enough to auto-flip this (real roofs don't consistently
    # follow along/across relative to the footprint). Only an explicit, user-supplied
    # choice changes the result.
    primary, roof_orientation_hint = orientation_mod.apply_roof_orientation_hint(primary, manual_orientation)

    return _LocatedBuilding(
        display_name=geocoded.display_name,
        lat=geocoded.lat,
        lon=geocoded.lon,
        ring_latlon=building.ring_latlon,
        primary=primary,
        groups=groups,
        roof_orientation_hint=roof_orientation_hint,
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def compute_orientation(address: str, manual_orientation: str | None = None) -> AzimuthResult:
    located = _locate_building(address, manual_orientation)
    return AzimuthResult(
        address=address,
        display_name=located.display_name,
        lat=located.lat,
        lon=located.lon,
        primary=located.primary,
        groups=located.groups,
        roof_orientation_hint=located.roof_orientation_hint,
        ridge_detected=False,
        image=None,
        image_attribution=None,
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def compute_building_azimuth(address: str, manual_orientation: str | None = None) -> AzimuthResult:
    located = _locate_building(address, manual_orientation)

    lats = [p[0] for p in located.ring_latlon]
    lons = [p[1] for p in located.ring_latlon]
    bbox_latlon = (min(lats), min(lons), max(lats), max(lons))

    ring_xy = geometry.project_to_local_meters(located.ring_latlon, origin=(located.lat, located.lon))
    xs = [p[0] for p in ring_xy]
    ys = [p[1] for p in ring_xy]
    bbox_diagonal_m = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    pad_m = max(8.0, 0.25 * bbox_diagonal_m)

    # NGI's Belgian orthophoto WMS is much higher resolution than Esri there
    # (and Esri's z20/21 coverage turned out to be unreliable placeholder
    # tiles in testing) -- prefer it in-region, falling back to Esri on any
    # failure (including addresses outside Belgium, where it's skipped).
    stitched = None
    image_attribution = imagery_mod.ESRI_ATTRIBUTION
    if wms_mod.is_in_belgium(located.lat, located.lon):
        try:
            stitched = wms_mod.fetch_wms_image(bbox_latlon, pad_m=pad_m, user_agent=USER_AGENT)
            image_attribution = wms_mod.NGI_ATTRIBUTION
        except wms_mod.WmsError:
            stitched = None
    if stitched is None:
        stitched = imagery_mod.fetch_stitched_image(bbox_latlon, pad_m=pad_m, user_agent=USER_AGENT)
        image_attribution = imagery_mod.ESRI_ATTRIBUTION

    # An explicit user choice always wins -- it reflects direct knowledge of
    # the real roof, which beats an uncertain automatic detection. Only look
    # for a ridge line when nothing was manually specified (a "flat" roof
    # explicitly has no ridge to find, so detection is skipped for it too).
    primary = located.primary
    ridge_detected = False
    if manual_orientation not in ("along", "across", "flat"):
        ring_px = imagery_mod.project_ring_to_pixels(located.ring_latlon, stitched)
        detected_line = ridge_mod.detect_ridge_line(stitched.image, ring_px)
        if detected_line is not None:
            mpp = imagery_mod.meters_per_pixel(located.lat, stitched.zoom)
            primary = ridge_mod.ridge_azimuth(detected_line, mpp)
            ridge_detected = True

    image = render_mod.draw_footprint_and_azimuth(stitched, located.ring_latlon, primary)

    return AzimuthResult(
        address=address,
        display_name=located.display_name,
        lat=located.lat,
        lon=located.lon,
        primary=primary,
        groups=located.groups,
        roof_orientation_hint=located.roof_orientation_hint,
        ridge_detected=ridge_detected,
        image=image,
        image_attribution=image_attribution,
    )
