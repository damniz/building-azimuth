"""Starlette route handlers exposing the azimuth pipeline as a JSON/PNG API.

Mounted alongside the Streamlit UI via `st.App(routes=..., exception_handlers=...)`
in asgi_app.py, so both surfaces share the same cached pipeline (azimuth/pipeline.py).
"""

from __future__ import annotations

from io import BytesIO

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .footprint import FootprintNotFoundError
from .geocode import GeocodeError
from .imagery import ImageryError
from .orientation import to_directional_azimuth
from .pipeline import compute_building_azimuth, compute_orientation


def _get_address(request: Request) -> str:
    address = request.query_params.get("address", "").strip()
    if not address:
        raise GeocodeError("Query parameter 'address' is required.")
    return address


def _get_manual_orientation(request: Request) -> str | None:
    value = request.query_params.get("roof_orientation")
    return value if value in ("along", "across", "flat") else None


def get_azimuth(request: Request) -> JSONResponse:
    address = _get_address(request)
    manual_orientation = _get_manual_orientation(request)
    result = compute_orientation(address, manual_orientation)
    return JSONResponse(
        {
            "address": result.address,
            "resolved_address": result.display_name,
            "lat": result.lat,
            "lon": result.lon,
            "azimuth_deg": to_directional_azimuth(result.primary.bearing_deg),
            "roof_orientation_hint": result.roof_orientation_hint,
            "azimuths": [
                {
                    "azimuth_deg": to_directional_azimuth(group.bearing_deg),
                    "length_fraction": group.length_fraction,
                }
                for group in result.groups
            ],
        }
    )


def get_azimuth_image(request: Request) -> Response:
    address = _get_address(request)
    manual_orientation = _get_manual_orientation(request)
    result = compute_building_azimuth(address, manual_orientation)
    buffer = BytesIO()
    result.image.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


async def handle_geocode_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


async def handle_not_found_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=404)


async def handle_imagery_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=502)


routes = [
    Route("/api/azimuth", get_azimuth),
    Route("/api/azimuth/image", get_azimuth_image),
]

exception_handlers = {
    GeocodeError: handle_geocode_error,
    FootprintNotFoundError: handle_not_found_error,
    ImageryError: handle_imagery_error,
}
