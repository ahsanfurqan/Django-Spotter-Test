from decimal import Decimal
from unittest import mock

import pytest
from django.core.management import CommandError, call_command

from fuel.management.commands.geocode_fuel_stations import load_city_cache, name_variants
from fuel.models import FuelStation
from routes.exceptions import GeocodingServiceError
from routes.services.geocoding import GeocodeResult

Status = FuelStation.GeocodeStatus
LOOKUP = "fuel.management.commands.geocode_fuel_stations.geocode_city"


def station(opis_id, city, state="TX", country="US"):
    return FuelStation.objects.create(
        opis_id=opis_id,
        name=f"S{opis_id}",
        address="I-10",
        city=city,
        state=state,
        country=country,
        rack_id=1,
        retail_price=Decimal("3.00"),
    )


def found(lat, lon):
    return GeocodeResult(latitude=lat, longitude=lon, display_name="x", country_code="us")


@pytest.fixture
def cache_file(tmp_path):
    return tmp_path / "city_coordinates.csv"


@pytest.mark.django_db
class TestGeocodeCommand:
    def test_geocodes_each_city_once_and_writes_cache(self, cache_file):
        station(1, "Austin"), station(2, "Austin"), station(3, "Nowhere")
        results = {"Austin": found(30.27, -97.74), "Nowhere": None}
        with mock.patch(LOOKUP, side_effect=lambda city, state: results[city]) as lookup:
            call_command("geocode_fuel_stations", cache_file=str(cache_file))
        assert lookup.call_count == 2
        austin = FuelStation.objects.filter(city="Austin")
        assert all(s.geocode_status == Status.OK and (s.latitude, s.longitude) == (30.27, -97.74) for s in austin)
        nowhere = FuelStation.objects.get(city="Nowhere")
        assert nowhere.geocode_status == Status.FAILED and not nowhere.has_coordinates
        assert load_city_cache(cache_file) == {("AUSTIN", "TX"): (30.27, -97.74), ("NOWHERE", "TX"): None}

    def test_resumes_without_repeating_lookups(self, cache_file):
        station(1, "Austin")
        with mock.patch(LOOKUP, return_value=found(30.27, -97.74)):
            call_command("geocode_fuel_stations", cache_file=str(cache_file))
        station(2, "Dallas")
        with mock.patch(LOOKUP, return_value=found(32.78, -96.8)) as lookup:
            call_command("geocode_fuel_stations", cache_file=str(cache_file))
        lookup.assert_called_once_with("Dallas", "TX")

    def test_offline_uses_cache_file_only(self, cache_file):
        cache_file.write_text("city,state,latitude,longitude\nAustin,TX,30.27,-97.74\n", encoding="utf-8")
        station(1, "austin"), station(2, "Dallas")
        with mock.patch(LOOKUP) as lookup:
            call_command("geocode_fuel_stations", cache_file=str(cache_file), offline=True)
        lookup.assert_not_called()
        assert FuelStation.objects.get(opis_id=1).geocode_status == Status.OK
        assert FuelStation.objects.get(opis_id=2).geocode_status == Status.PENDING

    def test_limit_stops_early(self, cache_file):
        station(1, "Austin"), station(2, "Dallas"), station(3, "Waco")
        with mock.patch(LOOKUP, return_value=found(31, -97)) as lookup:
            call_command("geocode_fuel_stations", cache_file=str(cache_file), limit=2)
        assert lookup.call_count == 2
        assert FuelStation.objects.filter(geocode_status=Status.PENDING).count() == 1

    def test_retry_failed(self, cache_file):
        cache_file.write_text("city,state,latitude,longitude\nNowhere,TX,,\n", encoding="utf-8")
        station(1, "Nowhere")
        call_command("geocode_fuel_stations", cache_file=str(cache_file), offline=True)
        assert FuelStation.objects.get().geocode_status == Status.FAILED
        with mock.patch(LOOKUP, return_value=found(31, -97)):
            call_command("geocode_fuel_stations", cache_file=str(cache_file), retry_failed=True)
        assert FuelStation.objects.get().geocode_status == Status.OK

    def test_skips_non_us_stations(self, cache_file):
        station(1, "Calgary", state="AB", country="CA")
        with mock.patch(LOOKUP) as lookup:
            call_command("geocode_fuel_stations", cache_file=str(cache_file))
        lookup.assert_not_called()

    def test_service_outage_aborts_with_progress_saved(self, cache_file, monkeypatch):
        monkeypatch.setattr("fuel.management.commands.geocode_fuel_stations.time.sleep", lambda s: None)
        station(1, "Austin")
        with mock.patch(LOOKUP, side_effect=GeocodingServiceError()), pytest.raises(CommandError, match="re-run"):
            call_command("geocode_fuel_stations", cache_file=str(cache_file))
        assert FuelStation.objects.get().geocode_status == Status.PENDING


@pytest.mark.parametrize(
    ("city", "expected"),
    [
        ("Mc Calla", ["Mc Calla", "McCalla"]),
        ("Ft Worth", ["Ft Worth", "Fort Worth"]),
        ("St. Louis", ["St. Louis", "Saint Louis"]),
        ("Mt Vernon", ["Mt Vernon", "Mount Vernon"]),
        ("La Place", ["La Place", "LaPlace"]),
        ("Oneill", ["Oneill", "O'neill"]),
        ("MC DERMITT", ["MC DERMITT", "MCDERMITT"]),
        ("Austin", ["Austin"]),
        ("Stanton", ["Stanton"]),
    ],
)
def test_name_variants(city, expected):
    assert name_variants(city) == expected


@pytest.mark.django_db
def test_lookup_tries_spelling_variants(cache_file):
    station(1, "Mc Calla", state="AL")
    results = {"Mc Calla": None, "McCalla": found(33.3, -87.0)}
    with mock.patch(LOOKUP, side_effect=lambda city, state: results[city]):
        call_command("geocode_fuel_stations", cache_file=str(cache_file))
    assert FuelStation.objects.get().geocode_status == Status.OK
