# Fuel Route Optimizer API

A Django REST API that plans a road trip between two places in the USA and tells you where to buy fuel.

You send a start and a finish. It returns the driving route (as GeoJSON you can drop onto a map), the fuel stops to make, how much to buy at each one, and what the trip costs in fuel. The vehicle is assumed to have a 500-mile range, get 10 MPG and start with a full tank. Prices come from the OPIS file in this repo (`fuel-prices-for-be-assessment.csv`).

Routing is done with [OSRM](https://project-osrm.org/) and geocoding with [Nominatim](https://nominatim.org/). Both are free OpenStreetMap services, so there are no API keys to set up.

## Requirements

- Docker with Docker Compose, **or**
- Python 3.12+ (PostgreSQL and Redis are optional for local runs, see below)

## Quick start with Docker

```bash
cp .env.example .env    # optional, every variable has a local default
docker compose up --build
```

That starts three containers: `web` (Django + gunicorn), `db` (PostgreSQL 17) and `redis`. On startup the web container runs the migrations, imports the CSV and loads station coordinates from `fuel/data/city_coordinates.csv`. This takes a few seconds and makes no external calls.

Once it's up, open http://localhost:8000/api/docs/ for the Swagger UI, or try it from the command line:

```bash
curl -X POST http://localhost:8000/api/v1/routes/optimize/ \
  -H "Content-Type: application/json" \
  -d '{"start": "New York, NY", "finish": "Chicago, IL"}'
```

If port 8000 is taken, run it on another one with `WEB_PORT=8010 docker compose up --build`.

The data commands can also be run by hand:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py import_fuel_stations
docker compose exec web python manage.py geocode_fuel_stations --offline
```

## Running locally without Docker

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

The quickest way to get going is SQLite with an in-memory cache:

```bash
export DB_ENGINE=sqlite DJANGO_DEBUG=true

python manage.py migrate
python manage.py import_fuel_stations
python manage.py geocode_fuel_stations --offline
python manage.py runserver
```

To use PostgreSQL and Redis instead, set the `POSTGRES_*` variables and `REDIS_URL` (see below) and leave `DB_ENGINE` unset.

`geocode_fuel_stations --offline` reads coordinates from the cache file in the repo. Without `--offline` it looks up any city that isn't in the file on Nominatim, at one request per second. You only need that if the CSV gains new cities.

## Configuration

Everything is configured through environment variables. Docker Compose picks them up from `.env` automatically. `.env.example` lists all of them with sensible defaults.

### Django

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | none | Required unless `DJANGO_DEBUG=true`. The app won't start without it. |
| `DJANGO_DEBUG` | `false` | |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated, e.g. `https://api.example.com`. |
| `DJANGO_SECURE_COOKIES` | `true` when not in debug | Set to `false` if you serve the admin over plain HTTP. |
| `DJANGO_SECURE_SSL_REDIRECT` | `false` | |
| `DJANGO_SECURE_HSTS_SECONDS` | `0` | |
| `DJANGO_BEHIND_TLS_PROXY` | `false` | Trust `X-Forwarded-Proto` from a reverse proxy. |
| `LOG_LEVEL` | `INFO` | |

### Database and cache

| Variable | Default | Notes |
|---|---|---|
| `DB_ENGINE` | `postgres` | Set to `sqlite` for a quick local run. |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `fuel_routes` / `fuel_routes` / empty | |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `localhost` / `5432` | Compose sets the host to `db`. |
| `SQLITE_PATH` | `./db.sqlite3` | Only used when `DB_ENGINE=sqlite`. |
| `REDIS_URL` | empty | e.g. `redis://localhost:6379/0`. When empty, an in-process memory cache is used. |
| `ROUTE_CACHE_TTL_SECONDS` | `86400` (1 day) | How long OSRM routes are cached. |
| `GEOCODE_CACHE_TTL_SECONDS` | `2592000` (30 days) | How long geocoding results are cached. |
| `GEOCODE_NEGATIVE_CACHE_TTL_SECONDS` | `3600` | How long a "place not found" answer is cached. |

### External services

| Variable | Default | Notes |
|---|---|---|
| `OSRM_BASE_URL` | `https://router.project-osrm.org` | Point at your own OSRM server for real traffic. |
| `NOMINATIM_BASE_URL` | `https://nominatim.openstreetmap.org` | Same for Nominatim. |
| `NOMINATIM_USER_AGENT` | `fuel-route-optimizer/1.0 (backend assessment)` | Nominatim's usage policy requires an identifying user agent. |
| `NOMINATIM_EMAIL` | empty | Optional contact address sent with requests. |
| `NOMINATIM_MIN_INTERVAL_SECONDS` | `1.0` | Minimum gap between Nominatim requests. Don't go below 1 on the public server. |
| `EXTERNAL_API_TIMEOUT_SECONDS` | `10` | Timeout for every OSRM/Nominatim call. |

### Fuel planning

| Variable | Default | Notes |
|---|---|---|
| `VEHICLE_MAX_RANGE_MILES` | `500` | |
| `VEHICLE_MPG` | `10` | Tank size is range ÷ MPG, so 50 gallons by default. |
| `FUEL_STATION_ROUTE_CORRIDOR_MILES` | `10` | How far from the road a station can be and still be considered. |
| `FUEL_STOP_PENALTY_DOLLARS` | `5` | A stop has to save at least this much fuel money to be worth making. `0` gives the absolute cheapest plan, however many stops it takes. |

### API and data loading

| Variable | Default | Notes |
|---|---|---|
| `API_ANON_THROTTLE_RATE` | `60/minute` | Per-client rate limit. Mostly there to protect the free upstream services. |
| `LOAD_FUEL_DATA_ON_START` | `true` | Docker only: import the CSV and load coordinates when the container starts. |
| `FUEL_PRICES_CSV` | `./fuel-prices-for-be-assessment.csv` | Default file for `import_fuel_stations`. |
| `CITY_COORDINATES_CSV` | `./fuel/data/city_coordinates.csv` | Coordinate cache used by `geocode_fuel_stations`. |
| `WEB_PORT` | `8000` | Docker only: host port for the API. |

## Running the tests

```bash
pytest
ruff check . && ruff format --check .
```

The suite runs in a couple of seconds and never touches the network: OSRM and Nominatim are faked. Inside Docker, use `docker compose exec web pytest`.
