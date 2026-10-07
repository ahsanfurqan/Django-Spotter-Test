from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import IntegrityError

from fuel.models import FuelStation

HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"


def write_csv(tmp_path: Path, body: str) -> str:
    path = tmp_path / "prices.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return str(path)


@pytest.mark.django_db
class TestImportCommand:
    def test_imports_rows_and_normalizes_fields(self, tmp_path):
        path = write_csv(
            tmp_path,
            '7,WOODSHED OF BIG CABIN,"I-44, EXIT 283 & US-69",Big Cabin,OK,307,3.00733333\n'
            "9,KWIK TRIP #796,I-94,Tomah                          ,wi,420,3.28733333\n",
        )
        call_command("import_fuel_stations", path)
        station = FuelStation.objects.get(opis_id=7)
        assert station.name == "WOODSHED OF BIG CABIN"
        assert station.address == "I-44, EXIT 283 & US-69"
        assert station.retail_price == Decimal("3.00733333")
        assert station.geocode_status == FuelStation.GeocodeStatus.PENDING
        assert not station.has_coordinates
        tomah = FuelStation.objects.get(opis_id=9)
        assert (tomah.city, tomah.state, tomah.country) == ("Tomah", "WI", "US")

    def test_duplicate_ids_keep_lowest_price(self, tmp_path):
        path = write_csv(
            tmp_path,
            "20,PILOT TRAVEL CENTER #1243,I-8,Gila Bend,AZ,930,3.899\n"
            "20,PILOT #1243,I-8,Gila Bend,AZ,930,3.799\n"
            "20,PILOT #1243,I-8,Gila Bend,AZ,930,3.999\n",
        )
        call_command("import_fuel_stations", path)
        assert FuelStation.objects.count() == 1
        assert FuelStation.objects.get().retail_price == Decimal("3.799")

    def test_invalid_rows_are_skipped(self, tmp_path, capsys):
        path = write_csv(
            tmp_path,
            "1,OK STATION,A,Town,TX,1,3.10\n"
            "2,NO PRICE,A,Town,TX,1,\n"
            "3,BAD PRICE,A,Town,TX,1,abc\n"
            "4,NEGATIVE,A,Town,TX,1,-1\n"
            "5,NAN PRICE,A,Town,TX,1,NaN\n"
            "x,BAD ID,A,Town,TX,1,3.00\n"
            "7,NO STATE,A,Town,,1,3.00\n",
        )
        call_command("import_fuel_stations", path)
        assert list(FuelStation.objects.values_list("opis_id", flat=True)) == [1]
        assert "6 invalid rows skipped" in capsys.readouterr().out

    def test_canadian_provinces_are_flagged(self, tmp_path):
        call_command("import_fuel_stations", write_csv(tmp_path, "1,ESSO,Hwy 1,Calgary,AB,1,4.10\n"))
        assert FuelStation.objects.get().country == "CA"

    def test_reimport_updates_prices_and_keeps_coordinates(self, tmp_path):
        call_command("import_fuel_stations", write_csv(tmp_path, "1,STATION,A,Town,TX,1,3.10\n"))
        FuelStation.objects.update(latitude=30.0, longitude=-97.0, geocode_status=FuelStation.GeocodeStatus.OK)
        call_command("import_fuel_stations", write_csv(tmp_path, "1,STATION,A,Town,TX,1,2.95\n"))
        station = FuelStation.objects.get()
        assert station.retail_price == Decimal("2.95")
        assert (station.latitude, station.longitude, station.geocode_status) == (30.0, -97.0, "ok")

    def test_missing_columns(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("id,name\n1,x\n")
        with pytest.raises(CommandError, match="missing columns"):
            call_command("import_fuel_stations", str(path))

    def test_missing_file(self, tmp_path):
        with pytest.raises(CommandError, match="not found"):
            call_command("import_fuel_stations", str(tmp_path / "nope.csv"))

    def test_imports_assessment_csv(self):
        call_command("import_fuel_stations", settings.FUEL_PRICES_CSV)
        assert FuelStation.objects.count() == 6738
        assert FuelStation.objects.filter(country="US").count() > 6000
        assert not FuelStation.objects.filter(retail_price__lte=0).exists()


@pytest.mark.django_db
class TestFuelStationModel:
    def test_str_and_coordinates(self):
        station = FuelStation.objects.create(
            opis_id=1,
            name="LOVES #1",
            address="I-40",
            city="Amarillo",
            state="TX",
            rack_id=1,
            retail_price=Decimal("3.1"),
        )
        assert str(station) == "LOVES #1 (Amarillo, TX) $3.1"
        assert not station.has_coordinates
        station.latitude, station.longitude = 35.2, -101.8
        assert station.has_coordinates

    def test_price_must_be_positive(self):
        with pytest.raises(IntegrityError):
            FuelStation.objects.create(
                opis_id=1, name="X", address="Y", city="Z", state="TX", rack_id=1, retail_price=Decimal("0")
            )

    def test_opis_id_is_unique(self):
        fields = {"name": "X", "address": "Y", "city": "Z", "state": "TX", "rack_id": 1, "retail_price": Decimal("3")}
        FuelStation.objects.create(opis_id=1, **fields)
        with pytest.raises(IntegrityError):
            FuelStation.objects.create(opis_id=1, **fields)
