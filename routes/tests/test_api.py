from decimal import Decimal

import pytest
import requests
from rest_framework.test import APIClient

from conftest import ROUTE_MILES, FakeResponse
from fuel.models import FuelStation

URL = "/api/v1/routes/optimize/"
LONG_TRIP = {"start": "West Town, KS", "finish": "East Town, OH"}  # ~1,058 miles
SHORT_TRIP = {"start": "West Town, KS", "finish": "Near Town, KS"}  # ~159 miles


@pytest.fixture
def client():
    return APIClient()


def post(client, body):
    return client.post(URL, body, format="json")


def assert_error(response, status, code):
    assert response.status_code == status, response.json()
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"]


@pytest.fixture
def corridor_stations(make_station):
    """Known stations along the synthetic 1,058-mile route."""
    return {
        "early_cheap": make_station(150, "2.90"),
        "mid_expensive": make_station(420, "4.50", offset_lat=0.05),
        "mid_cheap": make_station(480, "3.10", offset_lat=-0.08),
        "late": make_station(800, "3.40"),
        "far_off_route": make_station(470, "1.99", offset_lat=1.0),  # ~69 miles away
        "not_geocoded": make_station(
            460, "1.50", latitude=None, longitude=None, geocode_status=FuelStation.GeocodeStatus.PENDING
        ),
        "canadian": make_station(465, "1.75", country="CA", state="ON"),
    }


@pytest.mark.django_db
class TestOptimizeRoute:
    def test_long_trip_returns_optimal_multi_stop_plan(self, client, fake_http, corridor_stations):
        response = post(client, LONG_TRIP)
        assert response.status_code == 200, response.json()
        data = response.json()

        assert set(data) == {"start", "finish", "route", "vehicle", "fuel", "fuel_stops", "map", "meta"}
        assert data["route"]["distance_miles"] == pytest.approx(ROUTE_MILES, abs=0.01)
        assert data["route"]["geometry"]["type"] == "LineString"
        # The synthetic road is straight, so the returned (simplified) line keeps only its endpoints.
        assert data["route"]["geometry"]["coordinates"] == [[-100.0, 40.0], [-80.0, 40.0]]
        assert data["vehicle"] == {"max_range_miles": 500, "fuel_efficiency_mpg": 10, "tank_capacity_gallons": 50}

        stops = data["fuel_stops"]
        expected = [corridor_stations[k].id for k in ("mid_cheap", "late")]
        assert [s["station"]["id"] for s in stops] == expected
        # With the default $5 stop penalty, topping up at the $2.90 station (mile 150) saves only $3:
        # two stops ($175.32 + $10) beat three ($172.32 + $15). Fill at mile 480 ($3.10, cheapest
        # within reach; the $4.50 station is skipped), then buy only what is left at mile 800.
        assert stops[0]["fuel_before_stop_gallons"] == pytest.approx(2, abs=0.01)
        assert stops[0]["fuel_purchased_gallons"] == pytest.approx(48, abs=0.01)
        assert stops[0]["fuel_cost"] == pytest.approx(148.80)
        assert stops[1]["fuel_before_stop_gallons"] == pytest.approx(18, abs=0.01)
        last_leg = ROUTE_MILES - stops[1]["route_distance_miles"]
        assert stops[1]["fuel_purchased_gallons"] == pytest.approx(last_leg / 10 - 18, abs=0.01)
        assert data["meta"]["stop_penalty_dollars"] == 5

        fuel = data["fuel"]
        assert fuel["initial_fuel_gallons"] == 50
        assert fuel["total_fuel_required_gallons"] == pytest.approx(ROUTE_MILES / 10, abs=0.001)
        assert fuel["fuel_purchased_gallons"] == pytest.approx(fuel["total_fuel_required_gallons"] - 50, abs=0.002)
        assert fuel["fuel_at_destination_gallons"] == 0
        assert Decimal(str(fuel["total_cost"])) == sum(Decimal(str(s["fuel_cost"])) for s in stops)
        assert fuel["currency"] == "USD"

    def test_zero_stop_penalty_gives_pure_minimum_fuel_cost(self, client, fake_http, corridor_stations, settings):
        settings.FUEL_STOP_PENALTY_DOLLARS = Decimal(0)
        stops = post(client, LONG_TRIP).json()["fuel_stops"]
        expected = [corridor_stations[k].id for k in ("early_cheap", "mid_cheap", "late")]
        assert [s["station"]["id"] for s in stops] == expected
        # Top up 15 gal at $2.90 (arrive with 35), fill 33 gal at $3.10 (arrive with 17), finish at $3.40.
        assert [s["fuel_before_stop_gallons"] for s in stops] == pytest.approx([35, 17, 18], abs=0.01)
        assert [s["fuel_purchased_gallons"] for s in stops[:2]] == pytest.approx([15, 33], abs=0.01)
        assert stops[0]["fuel_cost"] == pytest.approx(43.50)

    def test_every_leg_respects_max_range(self, client, fake_http, corridor_stations):
        data = post(client, LONG_TRIP).json()
        previous = 0
        for stop in data["fuel_stops"]:
            assert stop["distance_from_previous_stop_miles"] == pytest.approx(stop["route_distance_miles"] - previous)
            assert stop["distance_from_previous_stop_miles"] <= 500
            previous = stop["route_distance_miles"]
        assert data["route"]["distance_miles"] - previous <= 500

    def test_map_shows_only_chosen_stops_by_default(self, client, fake_http, corridor_stations):
        data = post(client, LONG_TRIP).json()
        features = data["map"]["features"]
        assert [f["properties"]["marker"] for f in features] == ["start", "finish", "fuel_station", "fuel_station"]
        assert [f["properties"]["sequence"] for f in features[2:]] == [1, 2]
        assert all(f["properties"]["selected"] for f in features[2:])
        assert data["meta"]["candidate_station_count"] == 4

    def test_map_contains_markers_for_start_finish_and_stations(self, client, fake_http, corridor_stations):
        data = post(client, {**LONG_TRIP, "include_all_stations": True}).json()
        features = data["map"]["features"]
        assert data["map"]["type"] == "FeatureCollection"
        assert [f["properties"]["marker"] for f in features[:2]] == ["start", "finish"]
        stations = {f["properties"]["id"]: f for f in features[2:]}
        assert set(stations) == {corridor_stations[k].id for k in ("early_cheap", "mid_expensive", "mid_cheap", "late")}
        selected = {i for i, f in stations.items() if f["properties"]["selected"]}
        assert selected == {corridor_stations[k].id for k in ("mid_cheap", "late")}
        assert stations[corridor_stations["mid_cheap"].id]["properties"]["sequence"] == 1
        assert stations[corridor_stations["mid_expensive"].id]["properties"]["sequence"] is None
        assert stations[corridor_stations["late"].id]["geometry"]["type"] == "Point"
        assert data["meta"]["candidate_station_count"] == 4

    def test_short_trip_needs_no_fuel(self, client, fake_http, corridor_stations):
        data = post(client, SHORT_TRIP).json()
        assert data["fuel_stops"] == []
        assert data["fuel"]["total_cost"] == 0
        assert data["fuel"]["fuel_purchased_gallons"] == 0
        assert data["fuel"]["fuel_at_destination_gallons"] == pytest.approx(
            50 - data["route"]["distance_miles"] / 10, abs=0.01
        )

    def test_accepts_coordinates(self, client, fake_http, corridor_stations):
        response = post(client, {"start": {"lat": 40.0, "lng": -100.0}, "finish": {"lat": 40.0, "lng": -80.0}})
        assert response.status_code == 200, response.json()
        assert response.json()["start"]["input"] == {"lat": 40.0, "lng": -100.0}
        assert fake_http.count("/reverse") == 2

    def test_mixed_text_and_coordinates(self, client, fake_http, corridor_stations):
        response = post(client, {"start": "West Town, KS", "finish": {"lat": 40.0, "lng": -97.0}})
        assert response.status_code == 200

    def test_identical_request_uses_cache(self, client, fake_http, corridor_stations):
        first = post(client, LONG_TRIP).json()
        calls = len(fake_http.calls)
        second = post(client, LONG_TRIP).json()
        assert len(fake_http.calls) == calls  # no new geocoding or routing requests
        assert first["meta"]["route_cached"] is False and second["meta"]["route_cached"] is True
        assert first["fuel"] == second["fuel"]

    def test_plan_uses_a_single_database_query(self, client, fake_http, corridor_stations, django_assert_num_queries):
        with django_assert_num_queries(1):
            assert post(client, LONG_TRIP).status_code == 200

    def test_no_feasible_plan(self, client, fake_http, make_station):
        make_station(100, "3.00")  # nothing after mile 100 and the route is 1,058 miles
        response = post(client, LONG_TRIP)
        assert_error(response, 422, "NO_FEASIBLE_FUEL_PLAN")
        assert "route mile 100" in response.json()["error"]["message"]

    def test_unknown_location(self, client, fake_http):
        response = post(client, {"start": "Nowhere Special", "finish": "East Town, OH"})
        assert_error(response, 404, "LOCATION_NOT_FOUND")
        assert "start" in response.json()["error"]["message"]

    def test_coordinates_outside_usa(self, client, fake_http):
        fake_http.reverse_country = "mx"
        response = post(client, {"start": {"lat": 19.43, "lng": -99.13}, "finish": "East Town, OH"})
        assert_error(response, 400, "LOCATION_OUTSIDE_USA")

    def test_same_location_text(self, client, fake_http):
        assert_error(post(client, {"start": "West Town, KS", "finish": " west town, ks"}), 400, "INVALID_REQUEST")

    def test_same_location_after_geocoding(self, client, fake_http):
        response = post(client, {"start": "West Town, KS", "finish": {"lat": 40.0, "lng": -100.0}})
        assert_error(response, 400, "SAME_LOCATION")

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"start": "West Town, KS"},
            {"start": "", "finish": "East Town, OH"},
            {"start": "   ", "finish": "East Town, OH"},
            {"start": 42, "finish": "East Town, OH"},
            {"start": {"lat": 95, "lng": -100}, "finish": "East Town, OH"},
            {"start": {"lat": 40}, "finish": "East Town, OH"},
            {"start": "x" * 201, "finish": "East Town, OH"},
        ],
    )
    def test_invalid_requests(self, client, fake_http, body):
        response = post(client, body)
        assert_error(response, 400, "INVALID_REQUEST")
        assert "details" in response.json()["error"]
        assert fake_http.calls == []

    def test_routing_timeout(self, client, fake_http):
        fake_http.osrm_error = requests.Timeout("slow")
        assert_error(post(client, LONG_TRIP), 502, "ROUTING_SERVICE_ERROR")

    def test_no_road_route(self, client, fake_http):
        fake_http.osrm_error = FakeResponse({"code": "NoRoute"}, status_code=400)
        assert_error(post(client, LONG_TRIP), 422, "ROUTE_NOT_FOUND")

    def test_geocoding_timeout(self, client, fake_http):
        fake_http.nominatim_error = requests.Timeout("slow")
        assert_error(post(client, LONG_TRIP), 502, "GEOCODING_SERVICE_ERROR")

    def test_get_not_allowed(self, client):
        assert_error(client.get(URL), 405, "METHOD_NOT_ALLOWED")

    def test_malformed_json(self, client):
        response = client.post(URL, "{not json", content_type="application/json")
        assert_error(response, 400, "PARSE_ERROR")


@pytest.mark.django_db
def test_openapi_schema_documents_endpoint(client):
    response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    operation = response.json()["paths"][URL]["post"]
    assert {"200", "400", "404", "422", "502"} <= set(operation["responses"])
