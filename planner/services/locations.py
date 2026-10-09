"""
Turning user input into coordinates, with as few external calls as possible.

Accepted forms, tried in this order:
  {"lat": 41.88, "lon": -87.63}   coordinates            -> 0 API calls
  "41.88,-87.63"                    coordinates as text    -> 0 API calls
  "Chicago, IL" / "Chicago, Illinois"  known US town       -> 0 API calls
  anything else ("1600 Pennsylvania Ave NW, Washington DC") -> 1 Nominatim call (cached)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from .calls import ExternalCallCounter
from .geo import in_usa
from .places import PlaceIndex, normalize_place

logger = logging.getLogger(__name__)

COORDINATE_TEXT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class LocationError(Exception):
    """Base class for input that cannot be turned into a US location."""


class LocationNotFound(LocationError):
    pass


class LocationOutsideUSA(LocationError):
    pass


class GeocoderUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    latitude: float
    longitude: float
    resolved_by: str  # coordinates | place_table | nominatim

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "label": self.label,
            "lat": round(self.latitude, 6),
            "lon": round(self.longitude, 6),
            "resolved_by": self.resolved_by,
        }


def _from_coordinates(lat: float, lon: float, query: str) -> Location:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise LocationNotFound(f"'{query}' is not a valid latitude/longitude pair.")
    if not in_usa(lat, lon):
        raise LocationOutsideUSA(f"'{query}' is outside the USA. Start and finish must both be in the USA.")
    return Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")


def _from_place_table(text: str) -> Location | None:
    if "," not in text:
        return None
    city, state = (part.strip() for part in text.rsplit(",", 1))
    # Allow "Austin, TX 78701" and "Austin, TX, USA".
    state = re.sub(r"\s+\d{5}(-\d{4})?$", "", state)
    if state.upper() in {"USA", "US", "UNITED STATES"} and "," in city:
        city, state = (part.strip() for part in city.rsplit(",", 1))
    place = PlaceIndex.get().lookup(city, state)
    if place is None:
        return None
    return Location(text, place.label, place.latitude, place.longitude, "place_table")


def _from_nominatim(text: str, calls: ExternalCallCounter) -> Location:
    cache_key = f"geocode:{normalize_place(text)}"
    cached = cache.get(cache_key)
    if cached is None:
        conf = settings.GEOCODING
        calls.geocoding += 1
        try:
            response = requests.get(
                conf["NOMINATIM_URL"],
                params={"q": text, "countrycodes": "us", "format": "jsonv2", "limit": 1},
                headers={"User-Agent": conf["USER_AGENT"]},
                timeout=conf["TIMEOUT_SECONDS"],
            )
            response.raise_for_status()
            results = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Geocoding failed for %r: %s", text, exc)
            raise GeocoderUnavailable("The geocoding service is unavailable. Try 'City, ST' or lat,lon.") from exc
        cached = (
            {"label": results[0].get("display_name", text), "lat": float(results[0]["lat"]),
             "lon": float(results[0]["lon"])}
            if results else {}
        )
        cache.set(cache_key, cached)
    if not cached:
        raise LocationNotFound(f"Could not find a US location matching '{text}'.")
    location = Location(text, cached["label"], cached["lat"], cached["lon"], "nominatim")
    if not in_usa(location.latitude, location.longitude):
        raise LocationOutsideUSA(f"'{text}' is outside the USA.")
    return location


def resolve_location(value, calls: ExternalCallCounter) -> Location:
    """Resolve a start/finish value. See module docstring for accepted forms."""
    if isinstance(value, dict):
        lat = value.get("lat", value.get("latitude"))
        lon = value.get("lon", value.get("lng", value.get("longitude")))
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            raise LocationNotFound("Coordinates must look like {\"lat\": 41.88, \"lon\": -87.63}.") from None
        return _from_coordinates(lat, lon, f"{lat},{lon}")

    text = " ".join(str(value or "").split())
    if not text:
        raise LocationNotFound("Location is empty.")
    if match := COORDINATE_TEXT.match(text):
        return _from_coordinates(float(match.group(1)), float(match.group(2)), text)
    return _from_place_table(text) or _from_nominatim(text, calls)
