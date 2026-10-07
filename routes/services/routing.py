"""OSRM driving routes, cached by normalized start/finish coordinates."""

import logging
from dataclasses import asdict, dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from routes.exceptions import RouteNotFoundError, RoutingServiceError, SameLocationError

from .geo import METERS_PER_MILE, simplify_line
from .http import session

logger = logging.getLogger(__name__)

# About 11 m. It looks identical on a map and takes New York to Los Angeles from ~35,000 points
# down to ~6,000.
DISPLAY_TOLERANCE_DEGREES = 0.0001


@dataclass(frozen=True)
class Route:
    distance_miles: float
    duration_seconds: float
    coordinates: list[list[float]]  # full detail, GeoJSON order [longitude, latitude]; used for station projection
    display_coordinates: list[list[float]]  # simplified copy returned to clients


def route_cache_key(start: tuple[float, float], finish: tuple[float, float]) -> str:
    return f"route:v2:{start[0]:.5f}:{start[1]:.5f}:{finish[0]:.5f}:{finish[1]:.5f}"


def _fetch_route(start: tuple[float, float], finish: tuple[float, float]) -> Route:
    (start_lat, start_lng), (end_lat, end_lng) = start, finish
    url = f"{settings.OSRM_BASE_URL}/route/v1/driving/{start_lng:.6f},{start_lat:.6f};{end_lng:.6f},{end_lat:.6f}"
    params = {"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"}
    try:
        response = session.get(url, params=params, timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS)
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("event=routing_failed error=%r", exc)
        raise RoutingServiceError() from exc

    code = payload.get("code") if isinstance(payload, dict) else None
    if code in {"NoRoute", "NoSegment"}:
        raise RouteNotFoundError()
    if response.status_code != 200 or code != "Ok" or not payload.get("routes"):
        logger.warning("event=routing_bad_response status=%s code=%s", response.status_code, code)
        raise RoutingServiceError()

    best = payload["routes"][0]
    coordinates = best.get("geometry", {}).get("coordinates") or []
    if len(coordinates) < 2:
        raise RoutingServiceError()
    if best["distance"] <= 0:
        raise SameLocationError("Start and finish resolve to the same point on the road network.")
    return Route(
        distance_miles=best["distance"] / METERS_PER_MILE,
        duration_seconds=best["duration"],
        coordinates=coordinates,
        display_coordinates=simplify_line(coordinates, DISPLAY_TOLERANCE_DEGREES),
    )


def get_route(start: tuple[float, float], finish: tuple[float, float]) -> tuple[Route, bool]:
    """Return (route, served_from_cache) for (lat, lng) start and finish points."""
    key = route_cache_key(start, finish)
    hit = cache.get(key)
    if hit is not None:
        return Route(**hit), True
    route = _fetch_route(start, finish)
    cache.set(key, asdict(route), settings.ROUTE_CACHE_TTL_SECONDS)
    return route, False
