"""Building footprint lookup via the Overpass API (OpenStreetMap).

OpenStreetMap building outlines are used as the single, unified footprint
source for the whole supported area (Belgium, France, and elsewhere in
Europe) rather than branching per country -- Flanders' OSM buildings in
particular were bulk-imported from the official GRB reference dataset, so
they're cadastral-grade there, and coverage elsewhere in the region is
reasonable too.

The main public instance (`overpass-api.de`) is frequently slow, rate-limited,
or fully down (observed repeatedly during development, not a one-off). Rather
than retrying the same struggling instance -- which is both unlikely to help
and against Overpass's usage etiquette -- queries fall back through a short
list of independent public instances, each getting exactly one attempt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import requests

from . import geometry

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# private.coffee is FOSSGIS-adjacent (well-maintained, no rate limits per the
# OSM wiki) but shares enough infrastructure lineage with the main instance
# that both have been observed down at the same time. maps.mail.ru is on
# genuinely independent infrastructure, which is exactly what makes it a
# useful last resort when the others are having a bad day.
DEFAULT_OVERPASS_URLS: tuple[str, ...] = (
    OVERPASS_URL,
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

# Overpass `around:R` measures to the nearest point of an element's geometry,
# not its centroid -- for a large building with the address point mid-footprint,
# a small radius can miss it even though the point is inside. Start reasonably
# wide and only widen further on a genuine miss.
_INITIAL_RADIUS_M = 100
_FALLBACK_RADIUS_M = 250


class FootprintNotFoundError(Exception):
    """Raised when no building footprint can be found near a point."""


@dataclass
class Footprint:
    osm_id: int
    ring_latlon: list[tuple[float, float]]  # open ring: no duplicated closing vertex
    tags: dict[str, str]


def _query_overpass(
    query: str, user_agent: str, overpass_urls: Sequence[str], timeout: float
) -> dict:
    """POST `query` to each URL in `overpass_urls` in turn, returning the first
    successful JSON response. Each URL gets exactly one attempt -- no retries
    against a single instance (etiquette: don't hammer a struggling server).
    """
    last_error: Exception | None = None
    for url in overpass_urls:
        try:
            response = requests.post(
                url,
                data={"data": query},
                headers={"User-Agent": user_agent},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            continue

    raise FootprintNotFoundError(
        f"Could not reach any building-footprint service (tried {len(overpass_urls)}): {last_error}"
    )


def fetch_candidate_buildings(
    lat: float,
    lon: float,
    radius_m: int,
    user_agent: str,
    overpass_urls: Sequence[str] = DEFAULT_OVERPASS_URLS,
    timeout: float = 12.0,
) -> list[Footprint]:
    query = (
        f"[out:json][timeout:{int(timeout)}];"
        f'(way["building"](around:{radius_m},{lat},{lon}););'
        "out geom;"
    )
    payload = _query_overpass(query, user_agent, overpass_urls, timeout)

    candidates: list[Footprint] = []
    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        geom = element.get("geometry")
        if not geom:
            continue
        ring = [(node["lat"], node["lon"]) for node in geom]
        if len(ring) >= 2 and ring[0] == ring[-1]:
            ring = ring[:-1]  # drop the duplicated closing vertex
        if len(ring) < 3:
            continue
        candidates.append(
            Footprint(
                osm_id=element["id"],
                ring_latlon=ring,
                tags=element.get("tags", {}),
            )
        )
    return candidates


def select_building(point_latlon: tuple[float, float], candidates: list[Footprint]) -> Footprint:
    if not candidates:
        raise FootprintNotFoundError("No candidate buildings to select from.")

    projected = [
        geometry.project_to_local_meters(c.ring_latlon, origin=point_latlon)
        for c in candidates
    ]
    origin_xy = (0.0, 0.0)  # the point itself, in its own local frame

    containing = [
        (c, ring_xy)
        for c, ring_xy in zip(candidates, projected)
        if geometry.point_in_polygon(origin_xy, ring_xy)
    ]
    if containing:
        # Smallest-area tie-break: the smaller polygon is more likely to be
        # the specific building rather than a containing block/courtyard.
        best, _ = min(containing, key=lambda pair: geometry.polygon_area(pair[1]))
        return best

    # None contain the point: fall back to nearest by distance to the
    # boundary (not centroid -- centroid distance can misrank an elongated
    # nearby building behind a far-away compact one).
    best, _ = min(
        zip(candidates, projected),
        key=lambda pair: geometry.point_to_ring_distance(origin_xy, pair[1]),
    )
    return best


def find_building(
    lat: float,
    lon: float,
    user_agent: str,
    overpass_urls: Sequence[str] = DEFAULT_OVERPASS_URLS,
    timeout: float = 12.0,
) -> Footprint:
    candidates = fetch_candidate_buildings(
        lat, lon, _INITIAL_RADIUS_M, user_agent, overpass_urls, timeout
    )
    if not candidates:
        candidates = fetch_candidate_buildings(
            lat, lon, _FALLBACK_RADIUS_M, user_agent, overpass_urls, timeout
        )
    if not candidates:
        raise FootprintNotFoundError(
            f"No building footprint found near ({lat}, {lon})."
        )
    return select_building((lat, lon), candidates)
