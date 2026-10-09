"""
In-memory station index and route-corridor matching.

All ~6.6k stations are loaded from the database once per process into NumPy
arrays. Matching a route then takes milliseconds and needs no external calls:

1. Pre-filter stations to the route's bounding box (+ corridor).
2. Build a KD-tree over the densified route points (as 3D unit vectors).
3. For each station, find the nearest route point. Keep it if it is within
   the corridor; its mile marker is that route point's distance from start.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .geo import EARTH_RADIUS_MILES, DenseLine, chord_to_miles, miles_to_chord, to_unit_vectors


@dataclass(frozen=True)
class Candidate:
    """A station that lies along the route."""

    station: int  # row in StationIndex arrays
    mile: float  # distance from the start, along the route
    off_route_miles: float  # straight-line distance from the route line
    price: float


class StationIndex:
    _instance: "StationIndex | None" = None
    _lock = threading.Lock()

    def __init__(self, records: list[dict]):
        if not records:
            raise RuntimeError("No fuel stations loaded. Run `python manage.py migrate` (loads the station data).")
        self.records = records
        self.lat = np.array([r["latitude"] for r in records], dtype=float)
        self.lon = np.array([r["longitude"] for r in records], dtype=float)
        self.price = np.array([float(r["price"]) for r in records], dtype=float)
        self.xyz = to_unit_vectors(self.lat, self.lon)

    @classmethod
    def get(cls) -> "StationIndex":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    from planner.models import FuelStation

                    fields = ("opis_id", "name", "address", "city", "state", "price", "latitude", "longitude")
                    cls._instance = cls(list(FuelStation.objects.values(*fields)))
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def __len__(self) -> int:
        return len(self.records)

    def along_route(self, route: DenseLine, corridor_miles: float) -> list[Candidate]:
        """Stations within `corridor_miles` of the route, sorted by mile marker
        (cheapest first among stations at the same mile)."""
        pad_lat = np.degrees(corridor_miles / EARTH_RADIUS_MILES)
        max_abs_lat = min(float(np.max(np.abs(route.lat))) + pad_lat, 89.0)
        pad_lon = pad_lat / np.cos(np.radians(max_abs_lat))
        in_box = np.flatnonzero(
            (self.lat >= route.lat.min() - pad_lat) & (self.lat <= route.lat.max() + pad_lat)
            & (self.lon >= route.lon.min() - pad_lon) & (self.lon <= route.lon.max() + pad_lon)
        )
        if in_box.size == 0:
            return []

        tree = cKDTree(to_unit_vectors(route.lat, route.lon))
        chord, nearest = tree.query(self.xyz[in_box], distance_upper_bound=miles_to_chord(corridor_miles))
        hit = np.isfinite(chord)
        stations, chord, nearest = in_box[hit], chord[hit], nearest[hit]

        miles = route.miles[nearest]
        off = chord_to_miles(chord)
        order = np.lexsort((self.price[stations], miles))
        return [
            Candidate(int(stations[k]), float(miles[k]), float(off[k]), float(self.price[stations[k]]))
            for k in order
        ]
