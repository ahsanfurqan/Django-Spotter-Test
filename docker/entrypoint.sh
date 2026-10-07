#!/bin/sh
set -e

python manage.py migrate --noinput

if [ "${LOAD_FUEL_DATA_ON_START:-true}" = "true" ]; then
    # Safe to run on every start. Refreshes prices from the CSV, then fills in coordinates
    # from the cache file in the repo without calling Nominatim. Takes a few seconds.
    python manage.py import_fuel_stations
    python manage.py geocode_fuel_stations --offline
fi

exec "$@"
