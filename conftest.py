"""Shared test fixtures. OSRM and Nominatim are faked here so the tests never go online."""

from decimal import Decimal
from unittest import mock

import pytest
import requests
from django.core.cache import cache

from fuel.models import FuelStation
from routes.services.geo import METERS_PER_MILE, haversine_miles

# A synthetic east-west "highway" along latitude 40 from lon -100 to lon -80 (~1,058 miles).
ROUTE_LAT = 40.0
ROUTE_COORDS = [[round(-100 + i * 0.1, 4), ROUTE_LAT] for i in range(201)]
ROUTE_MILES = sum(haversine_miles(a[1], a[0], b[1], b[0]) for a, b in zip(ROUTE_COORDS, ROUTE_COORDS[1:], strict=False))
MILES_PER_LON_DEGREE = ROUTE_MILES / 20

PLACES = {
    "west town, ks": {"lat": "40.0", "lon": "-100.0", "display_name": "West Town, Kansas, United States"},
    "east town, oh": {"lat": "40.0", "lon": "-80.0", "display_name": "East Town, Ohio, United States"},
    "near town, ks": {"lat": "40.0", "lon": "-97.0", "display_name": "Near Town, Kansas, United States"},
}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeHttp:
    """Routes session.get() calls to canned Nominatim/OSRM responses and records them."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.osrm_error: Exception | FakeResponse | None = None
        self.nominatim_error: Exception | FakeResponse | None = None
        self.reverse_country = "us"

    def count(self, fragment: str) -> int:
        return sum(fragment in url for url, _ in self.calls)

    def __call__(self, url, params=None, headers=None, timeout=None):
        assert timeout, "every external call must set a timeout"
        self.calls.append((url, params or {}))
        if "/route/v1/driving/" in url:
            return self._osrm(url)
        return self._nominatim(url, params or {})

    def _nominatim(self, url, params):
        if isinstance(self.nominatim_error, Exception):
            raise self.nominatim_error
        if self.nominatim_error is not None:
            return self.nominatim_error
        if url.endswith("/reverse"):
            return FakeResponse(
                {
                    "lat": str(params["lat"]),
                    "lon": str(params["lon"]),
                    "display_name": "Somewhere",
                    "address": {"country_code": self.reverse_country},
                }
            )
        place = PLACES.get(" ".join(str(params.get("q", "")).lower().split()))
        if place is None:
            return FakeResponse([])
        state = place["display_name"].split(", ")[1]
        iso = {"Kansas": "US-KS", "Ohio": "US-OH"}[state]
        return FakeResponse([{**place, "address": {"country_code": "us", "ISO3166-2-lvl4": iso}}])

    def _osrm(self, url):
        if isinstance(self.osrm_error, Exception):
            raise self.osrm_error
        if self.osrm_error is not None:
            return self.osrm_error
        (lon1, _), (lon2, _) = (map(float, p.split(",")) for p in url.rsplit("/", 1)[1].split(";"))
        coords = [c for c in ROUTE_COORDS if min(lon1, lon2) - 1e-9 <= c[0] <= max(lon1, lon2) + 1e-9]
        if lon1 > lon2:
            coords = coords[::-1]
        miles = abs(lon2 - lon1) * MILES_PER_LON_DEGREE
        return FakeResponse(
            {
                "code": "Ok",
                "routes": [
                    {
                        "distance": miles * METERS_PER_MILE,
                        "duration": miles / 60 * 3600,
                        "geometry": {"type": "LineString", "coordinates": coords},
                    }
                ],
            }
        )


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def fake_http():
    fake = FakeHttp()
    with mock.patch("routes.services.http.session.get", side_effect=fake):
        yield fake


def lon_at_mile(mile: float) -> float:
    return -100 + mile / MILES_PER_LON_DEGREE


@pytest.fixture
def make_station(db):
    counter = iter(range(1, 10_000))

    def _make(mile: float, price: str, offset_lat: float = 0.0, **overrides) -> FuelStation:
        opis_id = next(counter)
        fields = {
            "opis_id": opis_id,
            "name": f"STATION {opis_id}",
            "address": f"I-70, EXIT {opis_id}",
            "city": "Testville",
            "state": "KS",
            "rack_id": 1,
            "retail_price": Decimal(price),
            "latitude": ROUTE_LAT + offset_lat,
            "longitude": lon_at_mile(mile),
            "geocode_status": FuelStation.GeocodeStatus.OK,
            **overrides,
        }
        return FuelStation.objects.create(**fields)

    return _make
