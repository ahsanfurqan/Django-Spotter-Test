import pytest
import requests

from conftest import FakeResponse
from routes.exceptions import GeocodingServiceError, RouteNotFoundError, RoutingServiceError, SameLocationError
from routes.services import geocoding, routing

START, FINISH = (40.0, -100.0), (40.0, -95.0)


class TestGeocoding:
    def test_geocode_returns_location_and_sends_policy_headers(self, fake_http):
        result = geocoding.geocode("West Town, KS")
        assert (result.latitude, result.longitude, result.country_code) == (40.0, -100.0, "us")
        url, params = fake_http.calls[0]
        assert url.endswith("/search") and params["countrycodes"] == "us" and params["limit"] == 1

    def test_geocode_is_cached_by_normalized_query(self, fake_http):
        geocoding.geocode("West Town, KS")
        geocoding.geocode("  west   town, ks ")
        assert len(fake_http.calls) == 1

    def test_not_found_is_negatively_cached(self, fake_http):
        assert geocoding.geocode("Atlantis") is None
        assert geocoding.geocode("Atlantis") is None
        assert len(fake_http.calls) == 1

    @pytest.mark.parametrize(
        "failure",
        [
            requests.Timeout("slow"),
            requests.ConnectionError("down"),
            FakeResponse({}, status_code=503),
            FakeResponse(ValueError("bad json")),
        ],
    )
    def test_upstream_failures_raise_service_error_and_are_not_cached(self, fake_http, failure):
        fake_http.nominatim_error = failure
        with pytest.raises(GeocodingServiceError):
            geocoding.geocode("West Town, KS")
        fake_http.nominatim_error = None
        assert geocoding.geocode("West Town, KS") is not None

    def test_reverse_geocode_reports_country(self, fake_http):
        fake_http.reverse_country = "ca"
        result = geocoding.reverse_geocode(49.0, -97.0)
        assert result.country_code == "ca"

    def test_city_lookup_falls_back_to_free_text(self, fake_http):
        result = geocoding.geocode_city("West Town", "KS")
        assert result is not None
        assert fake_http.count("/search") == 2  # structured miss, then free-text hit

    def test_city_lookup_rejects_same_named_town_in_another_state(self, fake_http):
        kansas = {"lat": "40", "lon": "-100", "address": {"country_code": "us", "ISO3166-2-lvl4": "US-KS"}}
        fake_http.nominatim_error = FakeResponse([kansas])  # every search answers with the Kansas town
        assert geocoding.geocode_city("West Town", "OH") is None
        assert geocoding.geocode_city("West Town", "KS").latitude == 40.0

    def test_requests_are_throttled(self, fake_http, settings, monkeypatch):
        settings.NOMINATIM_MIN_INTERVAL_SECONDS = 1.0
        sleeps = []
        monkeypatch.setattr(geocoding.time, "sleep", sleeps.append)
        geocoding.geocode("West Town, KS")
        geocoding.geocode("East Town, OH")
        assert sleeps and 0 < sleeps[-1] <= 1.0


class TestRouting:
    def test_route_is_parsed_into_miles(self, fake_http):
        route, cached = routing.get_route(START, FINISH)
        assert not cached
        assert route.distance_miles == pytest.approx(5 * 52.9, rel=0.01)
        assert route.coordinates[0] == [-100.0, 40.0] and route.coordinates[-1] == [-95.0, 40.0]
        url, params = fake_http.calls[0]
        assert "/route/v1/driving/-100.000000,40.000000;-95.000000,40.000000" in url
        assert params["geometries"] == "geojson" and params["overview"] == "full"

    def test_route_includes_simplified_display_geometry(self, fake_http):
        route, _ = routing.get_route(START, FINISH)
        assert len(route.coordinates) == 51  # full detail kept for station projection
        assert route.display_coordinates == [[-100.0, 40.0], [-95.0, 40.0]]  # straight road -> endpoints

    def test_route_is_cached(self, fake_http):
        routing.get_route(START, FINISH)
        route, cached = routing.get_route(START, FINISH)
        assert cached and route.distance_miles > 0
        assert fake_http.count("/route/") == 1

    def test_cache_key_is_deterministic(self):
        assert (
            routing.route_cache_key((40.123456, -100.0), (41.0, -90.5))
            == "route:v2:40.12346:-100.00000:41.00000:-90.50000"
        )

    def test_no_route(self, fake_http):
        fake_http.osrm_error = FakeResponse({"code": "NoRoute", "message": "Impossible route"}, status_code=400)
        with pytest.raises(RouteNotFoundError):
            routing.get_route(START, FINISH)

    @pytest.mark.parametrize(
        "failure",
        [
            requests.Timeout("slow"),
            requests.ConnectionError("down"),
            FakeResponse({"code": "TooBig"}, status_code=400),
            FakeResponse(ValueError("html error page"), status_code=502),
            FakeResponse({"code": "Ok", "routes": []}),
        ],
    )
    def test_failures_raise_routing_service_error(self, fake_http, failure):
        fake_http.osrm_error = failure
        with pytest.raises(RoutingServiceError):
            routing.get_route(START, FINISH)
        assert routing.cache.get(routing.route_cache_key(START, FINISH)) is None

    def test_zero_distance_route(self, fake_http):
        fake_http.osrm_error = FakeResponse(
            {
                "code": "Ok",
                "routes": [{"distance": 0, "duration": 0, "geometry": {"coordinates": [[-100, 40], [-100, 40]]}}],
            }
        )
        with pytest.raises(SameLocationError):
            routing.get_route(START, FINISH)
