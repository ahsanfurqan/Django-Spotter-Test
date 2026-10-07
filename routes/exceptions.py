import logging

from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class RoutePlanningError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "ROUTE_PLANNING_ERROR"
    default_detail = "The route could not be planned."


class SameLocationError(RoutePlanningError):
    default_code = "SAME_LOCATION"
    default_detail = "Start and finish resolve to the same location."


class LocationOutsideUSAError(RoutePlanningError):
    default_code = "LOCATION_OUTSIDE_USA"
    default_detail = "Location must be in the USA."


class LocationNotFoundError(RoutePlanningError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "LOCATION_NOT_FOUND"
    default_detail = "Could not resolve the location."


class RouteNotFoundError(RoutePlanningError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "ROUTE_NOT_FOUND"
    default_detail = "No driving route exists between the start and finish locations."


class NoFeasibleFuelPlanError(RoutePlanningError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "NO_FEASIBLE_FUEL_PLAN"
    default_detail = "No sequence of reachable fuel stations can complete this trip."


class GeocodingServiceError(RoutePlanningError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "GEOCODING_SERVICE_ERROR"
    default_detail = "The geocoding service is unavailable. Please retry later."


class RoutingServiceError(RoutePlanningError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "ROUTING_SERVICE_ERROR"
    default_detail = "The routing service is unavailable. Please retry later."


def _error_body(code: str, message: str, details=None) -> dict:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def api_exception_handler(exc, context):
    """Render every API error as {"error": {"code", "message", ["details"]}}."""
    response = exception_handler(exc, context)
    if response is None:
        logger.exception("event=unhandled_error view=%s", context.get("view").__class__.__name__)
        return Response(
            _error_body("INTERNAL_ERROR", "An unexpected error occurred."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if isinstance(exc, ValidationError):
        response.data = _error_body("INVALID_REQUEST", "The request body is invalid.", response.data)
    elif isinstance(exc, RoutePlanningError):
        response.data = _error_body(exc.default_code, str(exc.detail))
    else:
        code = getattr(exc, "default_code", "error")
        response.data = _error_body(str(code).upper(), str(getattr(exc, "detail", exc)))
    return response
