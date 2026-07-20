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


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _locate_building(address: str) -> _LocatedBuilding:
    geocoded = geocode_mod.geocode_address(address, user_agent=USER_AGENT)
    building = footprint_mod.find_building(geocoded.lat, geocoded.lon, user_agent=USER_AGENT)

    ring_xy = geometry.project_to_local_meters(building.ring_latlon, origin=(geocoded.lat, geocoded.lon))
    edges = orientation_mod.edge_bearings(ring_xy)
    primary = orientation_mod.dominant_azimuth(edges)
    groups = orientation_mod.group_azimuths(edges)

    return _LocatedBuilding(
        display_name=geocoded.display_name,
        lat=geocoded.lat,
        lon=geocoded.lon,
        ring_latlon=building.ring_latlon,
        primary=primary,
        groups=groups,
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def compute_orientation(address: str) -> AzimuthResult:
    located = _locate_building(address)
    return AzimuthResult(
        address=address,
        display_name=located.display_name,
        lat=located.lat,
        lon=located.lon,
        primary=located.primary,
        groups=located.groups,
        image=None,
        image_attribution=None,
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def compute_building_azimuth(address: str) -> AzimuthResult:
    located = _locate_building(address)

    lats = [p[0] for p in located.ring_latlon]
    lons = [p[1] for p in located.ring_latlon]
    bbox_latlon = (min(lats), min(lons), max(lats), max(lons))

    ring_xy = geometry.project_to_local_meters(located.ring_latlon, origin=(located.lat, located.lon))
    xs = [p[0] for p in ring_xy]
    ys = [p[1] for p in ring_xy]
    bbox_diagonal_m = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    pad_m = max(8.0, 0.25 * bbox_diagonal_m)

    stitched = imagery_mod.fetch_stitched_image(bbox_latlon, pad_m=pad_m, user_agent=USER_AGENT)
    image = render_mod.draw_footprint_and_azimuth(stitched, located.ring_latlon, located.primary)

    return AzimuthResult(
        address=address,
        display_name=located.display_name,
        lat=located.lat,
        lon=located.lon,
        primary=located.primary,
        groups=located.groups,
        image=image,
        image_attribution=imagery_mod.ESRI_ATTRIBUTION,
    )
