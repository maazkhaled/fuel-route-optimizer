"""Test helpers: build realistic OSRM responses without touching the network."""

from __future__ import annotations

from unittest import mock

import numpy as np

from planner.services.places import PlaceIndex


def encode_polyline(lat, lon, precision: int = 6) -> str:
    factor = 10 ** precision
    out = []
    prev_lat = prev_lon = 0
    for la, lo in zip(lat, lon):
        ilat, ilon = int(round(la * factor)), int(round(lo * factor))
        for delta in (ilat - prev_lat, ilon - prev_lon):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def route_through(*places: str) -> tuple[np.ndarray, np.ndarray]:
    """A polyline through the given "City, ST" places (straight legs)."""
    index = PlaceIndex.get()
    points = [index.lookup(*name.rsplit(",", 1)) for name in places]
    return np.array([p.latitude for p in points]), np.array([p.longitude for p in points])


def osrm_payload(lat, lon, distance_miles: float | None = None, duration_hours: float = 10.0) -> dict:
    from planner.services.geo import haversine_miles

    if distance_miles is None:
        distance_miles = float(np.sum(haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:])))
    return {
        "code": "Ok",
        "routes": [{
            "geometry": encode_polyline(lat, lon),
            "distance": distance_miles * 1609.344,
            "duration": duration_hours * 3600,
        }],
        "waypoints": [],
    }


def fake_response(payload: dict, status: int = 200) -> mock.Mock:
    response = mock.Mock(status_code=status)
    response.json.return_value = payload
    return response
