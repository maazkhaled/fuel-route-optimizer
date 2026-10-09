"""Maps service-layer errors to consistent HTTP responses.

Every error body has the same shape:
    {"error": {"code": "no_route", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .services.locations import GeocoderUnavailable, LocationNotFound, LocationOutsideUSA
from .services.optimizer import UnreachableSegmentError
from .services.routing import NoRouteFound, RoutingError
from .services.trip import SameStartAndFinish


def describe_service_error(exc: Exception) -> tuple[int, str, str, dict] | None:
    """(status, code, message, details) for known service errors, else None."""
    if isinstance(exc, SameStartAndFinish):
        return status.HTTP_400_BAD_REQUEST, "same_location", str(exc), {}
    if isinstance(exc, LocationOutsideUSA):
        return status.HTTP_400_BAD_REQUEST, "outside_usa", str(exc), {}
    if isinstance(exc, LocationNotFound):
        return status.HTTP_422_UNPROCESSABLE_ENTITY, "location_not_found", str(exc), {}
    if isinstance(exc, NoRouteFound):
        return status.HTTP_422_UNPROCESSABLE_ENTITY, "no_route", str(exc), {}
    if isinstance(exc, UnreachableSegmentError):
        return (
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "no_fuel_within_range",
            str(exc),
            {"gap_from_mile": round(exc.from_mile, 1), "gap_to_mile": round(exc.to_mile, 1),
             "vehicle_range_miles": exc.range_miles},
        )
    if isinstance(exc, GeocoderUnavailable):
        return status.HTTP_503_SERVICE_UNAVAILABLE, "geocoder_unavailable", str(exc), {}
    if isinstance(exc, RoutingError):
        return (
            status.HTTP_502_BAD_GATEWAY,
            "routing_unavailable",
            "The routing service is unavailable right now. Please try again shortly.",
            {},
        )
    return None


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    body = {"code": code, "message": message}
    if details:
        body["details"] = details
    return {"error": body}


def api_exception_handler(exc, context):
    described = describe_service_error(exc)
    if described:
        status_code, code, message, details = described
        return Response(error_body(code, message, details), status=status_code)

    response = exception_handler(exc, context)
    if response is not None:
        code = getattr(exc, "default_code", "error")
        if response.status_code == status.HTTP_400_BAD_REQUEST:
            response.data = error_body("invalid_request", "The request body is invalid.", response.data)
        else:
            response.data = error_body(code, str(response.data.get("detail", exc)))
    return response
