"""
Django settings for the Fuel Route Optimizer.

Everything that differs between environments is read from environment
variables with sensible local defaults, so `python manage.py runserver`
works out of the box with no configuration.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-local-dev-key-change-me-in-production",
)
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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


# SQLite keeps setup to zero steps for reviewers. The schema is plain
# relational data, so switching to PostgreSQL is a settings-only change.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Routes and geocoding results are cached so repeated requests (and the
# map view for a route that was just planned) make zero external calls.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route-optimizer",
        "TIMEOUT": 60 * 60 * 24,
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "planner.exceptions.api_exception_handler",
}


# --- Fuel planner -----------------------------------------------------------

FUEL_PLANNER = {
    # Vehicle model from the assignment.
    "VEHICLE_RANGE_MILES": float(os.environ.get("VEHICLE_RANGE_MILES", 500)),
    "VEHICLE_MPG": float(os.environ.get("VEHICLE_MPG", 10)),
    # The vehicle leaves with a full tank.
    "START_FUEL": os.environ.get("START_FUEL", "full"),
    # Stations are geocoded to their town, which can sit a few miles from the
    # highway exit, so a station counts as "on the route" within this band.
    "CORRIDOR_MILES": float(os.environ.get("CORRIDOR_MILES", 10)),
    # Each stop costs time and a detour. The planner minimises
    #   fuel cost + STOP_PENALTY_USD * stops
    # so it will not stop for half a gallon to save a cent. 0 = pure cheapest.
    "STOP_PENALTY_USD": float(os.environ.get("STOP_PENALTY_USD", 5)),
    # Route geometry is resampled to at most this spacing before matching.
    "DENSIFY_STEP_MILES": float(os.environ.get("DENSIFY_STEP_MILES", 0.5)),
    # Douglas-Peucker tolerance (degrees, ~0.0005 = 50 m) for the geometry
    # returned to clients. Matching always uses the full-resolution route.
    "GEOMETRY_SIMPLIFY_TOLERANCE": float(os.environ.get("GEOMETRY_SIMPLIFY_TOLERANCE", 0.0005)),
}

ROUTING = {
    # Public OSRM demo server: free, no API key. Point this at a self-hosted
    # OSRM instance for production traffic.
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    "OSRM_PROFILE": os.environ.get("OSRM_PROFILE", "driving"),
    # Optional fallback provider, used only if OSRM fails and a key is set.
    "ORS_API_KEY": os.environ.get("ORS_API_KEY", ""),
    "ORS_BASE_URL": os.environ.get("ORS_BASE_URL", "https://api.openrouteservice.org"),
    "TIMEOUT_SECONDS": float(os.environ.get("ROUTING_TIMEOUT_SECONDS", 20)),
    "CACHE_TTL_SECONDS": int(os.environ.get("ROUTING_CACHE_TTL_SECONDS", 60 * 60 * 24)),
}

GEOCODING = {
    # Used only for free-text locations that are not a known "City, ST".
    "NOMINATIM_URL": os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search"),
    "USER_AGENT": os.environ.get("GEOCODER_USER_AGENT", "fuel-route-optimizer/1.0 (assessment project)"),
    "TIMEOUT_SECONDS": float(os.environ.get("GEOCODER_TIMEOUT_SECONDS", 10)),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "planner": {"handlers": ["console"], "level": os.environ.get("PLANNER_LOG_LEVEL", "INFO")},
    },
}
