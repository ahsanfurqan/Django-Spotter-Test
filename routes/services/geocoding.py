"""Nominatim (OpenStreetMap) geocoding with Django-cache backed results and 1 req/s throttling."""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from routes.exceptions import GeocodingServiceError

from .http import session

logger = logging.getLogger(__name__)

_NOT_FOUND = {"found": False}
_throttle_lock = threading.Lock()
_last_request_at = 0.0


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str
    country_code: str


def _throttle() -> None:
    # This limit is per process, so three gunicorn workers could make three requests a second
    # between them. If that ever becomes a problem, move the lock into Redis.
    global _last_request_at
    with _throttle_lock:
        wait = settings.NOMINATIM_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _request(endpoint: str, params: dict) -> list | dict:
    params = {**params, "format": "jsonv2"}
    if settings.NOMINATIM_EMAIL:
        params["email"] = settings.NOMINATIM_EMAIL
    _throttle()
    try:
        response = session.get(
            f"{settings.NOMINATIM_BASE_URL}/{endpoint}",
            params=params,
            headers={"User-Agent": settings.NOMINATIM_USER_AGENT, "Accept-Language": "en"},
            timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("event=geocoding_failed endpoint=%s error=%r", endpoint, exc)
        raise GeocodingServiceError() from exc


def _cached(key: str, fetch) -> GeocodeResult | None:
    hit = cache.get(key)
    if hit is not None:
        return None if hit == _NOT_FOUND else GeocodeResult(**hit)
    result = fetch()
    if result is None:
        cache.set(key, _NOT_FOUND, settings.GEOCODE_NEGATIVE_CACHE_TTL_SECONDS)
    else:
        cache.set(key, result.__dict__, settings.GEOCODE_CACHE_TTL_SECONDS)
    return result


def _parse_place(place: dict) -> GeocodeResult:
    return GeocodeResult(
        latitude=float(place["lat"]),
        longitude=float(place["lon"]),
        display_name=place.get("display_name", ""),
        country_code=place.get("address", {}).get("country_code", "").lower(),
    )


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def geocode(query: str) -> GeocodeResult | None:
    """Resolve free text (city, address, ...) to a US location, or None if nothing matches."""

    def fetch() -> GeocodeResult | None:
        places = _request("search", {"q": query, "countrycodes": "us", "addressdetails": 1, "limit": 1})
        return _parse_place(places[0]) if places else None

    digest = hashlib.sha256(_normalize(query).encode()).hexdigest()
    return _cached(f"geocode:v1:search:{digest}", fetch)


def geocode_city(city: str, state: str) -> GeocodeResult | None:
    """Structured US city lookup used to locate fuel stations; falls back to free text.

    Nominatim sometimes answers with a same-named town in another state, so only results whose
    ISO 3166-2 code matches the requested state are accepted.
    """
    iso_code = f"US-{state.upper()}"

    def in_state(places: list) -> dict | None:
        return next((p for p in places if p.get("address", {}).get("ISO3166-2-lvl4") == iso_code), None)

    def fetch() -> GeocodeResult | None:
        common = {"countrycodes": "us", "addressdetails": 1, "limit": 5}
        place = in_state(_request("search", {"city": city, "state": state, **common}))
        if place is None:
            place = in_state(_request("search", {"q": f"{city}, {state}", **common}))
        return _parse_place(place) if place else None

    digest = hashlib.sha256(_normalize(f"{city}|{state}").encode()).hexdigest()
    return _cached(f"geocode:v1:city:v2:{digest}", fetch)


def reverse_geocode(latitude: float, longitude: float) -> GeocodeResult | None:
    """Look up the place at a coordinate (used to confirm user coordinates are inside the USA)."""

    def fetch() -> GeocodeResult | None:
        place = _request("reverse", {"lat": latitude, "lon": longitude, "zoom": 10, "addressdetails": 1})
        if not isinstance(place, dict) or "error" in place or "lat" not in place:
            return None
        return GeocodeResult(
            latitude=latitude,
            longitude=longitude,
            display_name=place.get("display_name", ""),
            country_code=place.get("address", {}).get("country_code", "").lower(),
        )

    return _cached(f"geocode:v1:reverse:{latitude:.5f}:{longitude:.5f}", fetch)
