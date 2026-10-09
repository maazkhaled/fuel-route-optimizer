"""
Routing client: one call to a free routing API per (uncached) trip.

Primary: OSRM public server (no API key). We ask for the full-resolution
geometry as polyline6, which is far smaller on the wire than GeoJSON.
Fallback: OpenRouteService, used only if OSRM fails and ORS_API_KEY is set.
Results are cached, so the map view for a route that was just planned (or any
repeated request) makes no external call at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from .calls import ExternalCallCounter
from .geo import METERS_PER_MILE, decode_polyline

logger = logging.getLogger(__name__)


class RoutingError(Exception):
    """The routing service failed (network, rate limit, 5xx)."""


class NoRouteFound(Exception):
    """The routing service answered, but there is no drivable route."""


@dataclass(frozen=True)
class Route:
    lat: np.ndarray
    lon: np.ndarray
    distance_miles: float
    duration_seconds: float
    provider: str


_session = requests.Session()


def _osrm_route(start: tuple[float, float], finish: tuple[float, float]) -> Route:
    conf = settings.ROUTING
    url = (
        f"{conf['OSRM_BASE_URL'].rstrip('/')}/route/v1/{conf['OSRM_PROFILE']}/"
        f"{start[1]:.6f},{start[0]:.6f};{finish[1]:.6f},{finish[0]:.6f}"
    )
    params = {"overview": "full", "geometries": "polyline6", "alternatives": "false", "steps": "false"}
    try:
        response = _session.get(url, params=params, timeout=conf["TIMEOUT_SECONDS"])
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"OSRM request failed: {exc}") from exc

    code = payload.get("code")
    if code in {"NoRoute", "NoSegment"}:
        raise NoRouteFound("No drivable route exists between these locations.")
    if response.status_code != 200 or code != "Ok" or not payload.get("routes"):
        raise RoutingError(f"OSRM error {response.status_code}: {payload.get('message') or code}")

    best = payload["routes"][0]
    lat, lon = decode_polyline(best["geometry"], precision=6)
    return Route(lat, lon, best["distance"] / METERS_PER_MILE, best["duration"], "osrm")


def _ors_route(start: tuple[float, float], finish: tuple[float, float]) -> Route:
    conf = settings.ROUTING
    try:
        response = _session.get(
            f"{conf['ORS_BASE_URL'].rstrip('/')}/v2/directions/driving-car",
            params={
                "api_key": conf["ORS_API_KEY"],
                "start": f"{start[1]:.6f},{start[0]:.6f}",
                "end": f"{finish[1]:.6f},{finish[0]:.6f}",
            },
            timeout=conf["TIMEOUT_SECONDS"],
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"OpenRouteService request failed: {exc}") from exc
    if response.status_code == 404 or (payload.get("error") or {}).get("code") in {2009, 2010}:
        raise NoRouteFound("No drivable route exists between these locations.")
    if response.status_code != 200 or not payload.get("features"):
        raise RoutingError(f"OpenRouteService error {response.status_code}: {payload.get('error')}")

    feature = payload["features"][0]
    coords = np.asarray(feature["geometry"]["coordinates"], dtype=float)
    summary = feature["properties"]["summary"]
    return Route(coords[:, 1], coords[:, 0], summary["distance"] / METERS_PER_MILE, summary["duration"], "openrouteservice")


def get_route(start: tuple[float, float], finish: tuple[float, float], calls: ExternalCallCounter) -> Route:
    """Driving route between two (lat, lon) points, from cache when possible."""
    key = f"route:{start[0]:.5f},{start[1]:.5f};{finish[0]:.5f},{finish[1]:.5f}"
    route = cache.get(key)
    if route is not None:
        return route

    calls.routing += 1
    try:
        route = _osrm_route(start, finish)
    except RoutingError as exc:
        if not settings.ROUTING["ORS_API_KEY"]:
            raise
        logger.warning("%s; falling back to OpenRouteService", exc)
        calls.routing += 1
        route = _ors_route(start, finish)

    if route.lat.size < 2:
        raise NoRouteFound("The routing service returned an empty route.")
    cache.set(key, route, settings.ROUTING["CACHE_TTL_SECONDS"])
    return route
