"""Address -> coordinates via the Nominatim (OpenStreetMap) search API.

Respects Nominatim's usage policy: a single request per lookup (no
client-side retry/hammering), a descriptive User-Agent identifying this app
and a contact address, and results cached by the caller (see
azimuth/pipeline.py) rather than re-fetched on every rerun.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


class GeocodeError(Exception):
    """Raised when an address cannot be resolved to coordinates."""


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    display_name: str


def geocode_address(address: str, user_agent: str, timeout: float = 10.0) -> GeocodeResult:
    address = address.strip()
    if not address:
        raise GeocodeError("Address must not be empty.")

    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )
        response.raise_for_status()
        results = response.json()
    except requests.RequestException as exc:
        raise GeocodeError(f"Could not reach the geocoding service: {exc}") from exc

    if not results:
        raise GeocodeError(f"No location found for address: {address!r}")

    top = results[0]
    return GeocodeResult(
        lat=float(top["lat"]),
        lon=float(top["lon"]),
        display_name=top.get("display_name", address),
    )
