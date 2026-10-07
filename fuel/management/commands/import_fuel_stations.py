import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuel.models import FuelStation

REQUIRED_COLUMNS = {"OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price"}
CANADIAN_PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}
UPDATE_FIELDS = ["name", "address", "city", "state", "country", "rack_id", "retail_price", "updated_at"]


def parse_row(row: dict) -> FuelStation | None:
    try:
        price = Decimal(row["Retail Price"].strip())
        opis_id = int(row["OPIS Truckstop ID"])
        rack_id = int(row["Rack ID"])
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return None
    state = (row["State"] or "").strip().upper()
    city = " ".join((row["City"] or "").split())
    if not price.is_finite() or price <= 0 or not state or not city:
        return None
    return FuelStation(
        opis_id=opis_id,
        name=row["Truckstop Name"].strip(),
        address=row["Address"].strip(),
        city=city,
        state=state,
        country="CA" if state in CANADIAN_PROVINCES else "US",
        rack_id=rack_id,
        retail_price=price,
    )


def read_stations(path: Path) -> tuple[list[FuelStation], int, int]:
    """Parse the CSV, returning (stations, total_rows, skipped_rows).

    The CSV repeats some OPIS IDs with different retail prices for the same site; we keep a
    single station per ID at its lowest posted price.
    """
    by_id: dict[int, FuelStation] = {}
    total = skipped = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise CommandError(f"CSV is missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            total += 1
            station = parse_row(row)
            if station is None:
                skipped += 1
                continue
            current = by_id.get(station.opis_id)
            if current is None or station.retail_price < current.retail_price:
                by_id[station.opis_id] = station
    return list(by_id.values()), total, skipped


class Command(BaseCommand):
    help = "Import (upsert) fuel stations and prices from the OPIS CSV. Safe to re-run; keeps existing coordinates."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default=settings.FUEL_PRICES_CSV)

    def handle(self, *args, csv_path: str, **options):
        path = Path(csv_path)
        if not path.is_file():
            raise CommandError(f"CSV file not found: {path}")

        stations, total, skipped = read_stations(path)
        # If a station's city changes between imports it keeps its old coordinates. That doesn't
        # happen in this data; if it starts to, reset geocode_status for the changed rows.
        with transaction.atomic():
            FuelStation.objects.bulk_create(
                stations,
                batch_size=1000,
                update_conflicts=True,
                unique_fields=["opis_id"],
                update_fields=UPDATE_FIELDS,
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(stations)} stations from {total} rows "
                f"({skipped} invalid rows skipped, {total - skipped - len(stations)} duplicate rows merged)."
            )
        )
