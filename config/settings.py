import os
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")
    SECRET_KEY = "insecure-dev-only-key"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "fuel",
    "routes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.environ.get("DB_ENGINE", "postgres") == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "fuel_routes"),
            "USER": os.environ.get("POSTGRES_USER", "fuel_routes"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
        }
    }

REDIS_URL = os.environ.get("REDIS_URL", "")
CACHES = {
    "default": (
        {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL, "KEY_PREFIX": "fuelroutes"}
        if REDIS_URL
        else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Security
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT")
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
if env_bool("DJANGO_BEHIND_TLS_PROXY"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "routes.exceptions.api_exception_handler",
    # Mainly here to protect the free OSRM and Nominatim servers from being hammered through us.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": os.environ.get("API_ANON_THROTTLE_RATE", "60/minute")},
    "COERCE_DECIMAL_TO_STRING": False,
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Fuel Route Optimizer API",
    "DESCRIPTION": "Plans a US driving route and the cheapest feasible fuel stops (500-mile range, 10 MPG).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Syntax highlighting a large JSON response makes the browser tab hang.
    "SWAGGER_UI_SETTINGS": {"syntaxHighlight": False},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"kv": {"format": "ts=%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "kv"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

# External services
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")
NOMINATIM_BASE_URL = os.environ.get("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org").rstrip("/")
NOMINATIM_USER_AGENT = os.environ.get("NOMINATIM_USER_AGENT", "fuel-route-optimizer/1.0 (backend assessment)")
NOMINATIM_EMAIL = os.environ.get("NOMINATIM_EMAIL", "")
# Nominatim's usage policy allows at most 1 request/second.
NOMINATIM_MIN_INTERVAL_SECONDS = float(os.environ.get("NOMINATIM_MIN_INTERVAL_SECONDS", "1.0"))
EXTERNAL_API_TIMEOUT_SECONDS = float(os.environ.get("EXTERNAL_API_TIMEOUT_SECONDS", "10"))
GEOCODE_CACHE_TTL_SECONDS = int(os.environ.get("GEOCODE_CACHE_TTL_SECONDS", str(30 * 24 * 3600)))
GEOCODE_NEGATIVE_CACHE_TTL_SECONDS = int(os.environ.get("GEOCODE_NEGATIVE_CACHE_TTL_SECONDS", "3600"))
ROUTE_CACHE_TTL_SECONDS = int(os.environ.get("ROUTE_CACHE_TTL_SECONDS", str(24 * 3600)))

# Fuel planning
FUEL_STATION_ROUTE_CORRIDOR_MILES = float(os.environ.get("FUEL_STATION_ROUTE_CORRIDOR_MILES", "10"))
# A stop must save at least this many dollars of fuel to be worth making (0 = pure minimum fuel cost).
FUEL_STOP_PENALTY_DOLLARS = Decimal(os.environ.get("FUEL_STOP_PENALTY_DOLLARS", "5"))
if not FUEL_STOP_PENALTY_DOLLARS.is_finite() or FUEL_STOP_PENALTY_DOLLARS < 0:
    raise ImproperlyConfigured("FUEL_STOP_PENALTY_DOLLARS must be a non-negative number.")
VEHICLE_MAX_RANGE_MILES = int(os.environ.get("VEHICLE_MAX_RANGE_MILES", "500"))
VEHICLE_MPG = int(os.environ.get("VEHICLE_MPG", "10"))
FUEL_PRICES_CSV = os.environ.get("FUEL_PRICES_CSV", str(BASE_DIR / "fuel-prices-for-be-assessment.csv"))
CITY_COORDINATES_CSV = os.environ.get("CITY_COORDINATES_CSV", str(BASE_DIR / "fuel" / "data" / "city_coordinates.csv"))
