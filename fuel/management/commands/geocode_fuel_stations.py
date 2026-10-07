import csv
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fuel.models import FuelStation
from routes.exceptions import GeocodingServiceError
from routes.services.geocoding import geocode_city

Status = FuelStation.GeocodeStatus
LOOKUP_CONCURRENCY = 3
CacheKey = tuple[str, str]
Coordinates = tuple[float, float] | None


def city_key(city: str, state: str) -> CacheKey:
    return " ".join(city.upper().split()), state.upper()


def name_variants(city: str) -> list[str]:
    """Spellings to try, in order, when the CSV name differs from OSM's.

    The CSV abbreviates and drops punctuation: "Ft Worth" (Fort Worth), "Mc Calla" (McCalla),
    "La Place" (LaPlace), "Oneill" (O'Neill). Variants are only tried after the literal name fails.
    """
    expanded = city
    for abbreviation, full in (("Ft", "Fort"), ("Mt", "Mount"), ("St", "Saint"), ("Pt", "Port")):
        expanded = re.sub(rf"\b{abbreviation}\.? ", f"{full} ", expanded, flags=re.IGNORECASE)
    joined = re.sub(r"\b(Mc|La|De) (?=\w)", r"\1", expanded, flags=re.IGNORECASE)
    apostrophe = re.sub(r"^O(?=[a-z]{3})", "O'", joined, flags=re.IGNORECASE)
    return list(dict.fromkeys([city, expanded, joined, apostrophe]))


def load_city_cache(path: Path) -> dict[CacheKey, Coordinates]:
    """Read previously geocoded city/state pairs. Empty lat/lon means "looked up, not found"."""
    if not path.is_file():
        return {}
    cache: dict[CacheKey, Coordinates] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            coords = (float(row["latitude"]), float(row["longitude"])) if row["latitude"] else None
            cache[city_key(row["city"], row["state"])] = coords
    return cache


def append_city_cache(path: Path, city: str, state: str, coords: Coordinates) -> None:
    is_new = not path.is_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(["city", "state", "latitude", "longitude"])
        writer.writerow([city, state, *(coords or ("", ""))])


class Command(BaseCommand):
    help = (
        "Populate station coordinates by geocoding each distinct US city/state once (Nominatim, 1 req/s). "
        "Resumable: only stations still pending are processed, and every lookup is appended to a CSV cache "
        "so later runs (or other environments) never repeat it."
    )

    def add_arguments(self, parser):
        parser.add_argument("--cache-file", default=settings.CITY_COORDINATES_CSV)
        parser.add_argument("--offline", action="store_true", help="Only use the cache file; never call Nominatim.")
        parser.add_argument("--limit", type=int, default=None, help="Max Nominatim lookups in this run.")
        parser.add_argument("--retry-failed", action="store_true", help="Retry cities previously not found.")

    def handle(self, *args, cache_file: str, offline: bool, limit: int | None, retry_failed: bool, **options):
        cache_path = Path(cache_file)
        cache = load_city_cache(cache_path)
        statuses = [Status.PENDING, Status.FAILED] if retry_failed else [Status.PENDING]
        pending = FuelStation.objects.filter(country="US", geocode_status__in=statuses)
        pairs = list(pending.values_list("city", "state").distinct().order_by("state", "city"))
        self.stdout.write(f"{len(pairs)} city/state pairs to resolve ({len(cache)} cached).")

        located = 0

        def apply(city: str, state: str, coords: Coordinates) -> None:
            nonlocal located
            updated = pending.filter(city=city, state=state).update(
                latitude=coords[0] if coords else None,
                longitude=coords[1] if coords else None,
                geocode_status=Status.OK if coords else Status.FAILED,
            )
            located += updated if coords else 0

        missing = []
        for city, state in pairs:
            key = city_key(city, state)
            if key in cache and (cache[key] is not None or not retry_failed):
                apply(city, state, cache[key])
            else:
                missing.append((city, state))

        if offline:
            missing = []
        elif limit is not None and len(missing) > limit:
            self.stdout.write(f"Lookup limit {limit} reached; re-run to continue.")
            missing = missing[:limit]

        # Nominatim takes 1-3 s to answer, so a few lookups run at once. The client's throttle
        # still makes sure requests start at least a second apart, as the usage policy asks.
        lookups = 0
        with ThreadPoolExecutor(max_workers=LOOKUP_CONCURRENCY) as pool:
            for (city, state), coords in zip(missing, pool.map(lambda pair: self._lookup(*pair), missing), strict=True):
                lookups += 1
                cache[city_key(city, state)] = coords
                append_city_cache(cache_path, city, state, coords)
                apply(city, state, coords)
                if lookups % 100 == 0:
                    self.stdout.write(f"  {lookups}/{len(missing)} lookups done...")

        remaining = FuelStation.objects.filter(country="US", geocode_status=Status.PENDING).count()
        failed = FuelStation.objects.filter(country="US", geocode_status=Status.FAILED).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Located {located} stations ({lookups} Nominatim lookups). "
                f"Still pending: {remaining}. Not found: {failed}."
            )
        )

    def _lookup(self, city: str, state: str) -> Coordinates:
        for attempt in range(3):
            try:
                for name in name_variants(city):
                    result = geocode_city(name, state)
                    if result:
                        return result.latitude, result.longitude
                return None
            except GeocodingServiceError:
                if attempt == 2:
                    raise CommandError("Nominatim keeps failing; progress is saved, re-run to resume.") from None
                time.sleep(5 * (attempt + 1))
        return None
