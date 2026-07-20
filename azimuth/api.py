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
from .pipeline import compute_building_azimuth, compute_orientation


def _get_address(request: Request) -> str:
    address = request.query_params.get("address", "").strip()
    if not address:
        raise GeocodeError("Query parameter 'address' is required.")
    return address


def get_azimuth(request: Request) -> JSONResponse:
    address = _get_address(request)
    result = compute_orientation(address)
    return JSONResponse(
        {
            "address": result.address,
            "resolved_address": result.display_name,
            "lat": result.lat,
            "lon": result.lon,
            "azimuth_deg": result.primary.bearing_deg,
            "azimuth_deg_alt": (result.primary.bearing_deg + 180.0) % 360.0,
            "azimuths": [
                {"azimuth_deg": group.bearing_deg, "length_fraction": group.length_fraction}
                for group in result.groups
            ],
        }
    )


def get_azimuth_image(request: Request) -> Response:
    address = _get_address(request)
    result = compute_building_azimuth(address)
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
