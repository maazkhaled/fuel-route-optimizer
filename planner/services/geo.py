"""Small, dependency-light geometry helpers (NumPy only)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles. Works on scalars or NumPy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def to_unit_vectors(lat, lon) -> np.ndarray:
    """Lat/lon in degrees -> 3D unit vectors, so a KD-tree can do spherical
    nearest-neighbour search with plain Euclidean (chord) distances."""
    lat, lon = np.radians(lat), np.radians(lon)
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def miles_to_chord(miles: float) -> float:
    return 2.0 * np.sin(miles / (2.0 * EARTH_RADIUS_MILES))


def chord_to_miles(chord):
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.clip(np.asarray(chord) / 2.0, 0.0, 1.0))


@dataclass(frozen=True)
class DenseLine:
    """A route resampled so consecutive points are at most `step` miles apart.

    `miles[i]` is the distance along the route from the start to point i.
    """

    lat: np.ndarray
    lon: np.ndarray
    miles: np.ndarray

    @property
    def total_miles(self) -> float:
        return float(self.miles[-1])


def densify(lat: np.ndarray, lon: np.ndarray, step_miles: float, total_miles: float | None = None) -> DenseLine:
    """Insert points so no gap exceeds `step_miles`.

    Routing engines only emit vertices where the road bends, so a straight
    interstate can have vertices miles apart. Resampling makes "nearest route
    point" a good proxy for "nearest point on the road".

    If `total_miles` is given (the routing engine's own distance), the
    along-route mileage is scaled to match it exactly.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    if lat.size < 2:
        raise ValueError("A route needs at least two points.")

    seg = haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:])
    cum = np.concatenate(([0.0], np.cumsum(seg)))

    parts = np.maximum(1, np.ceil(seg / step_miles).astype(int))
    seg_idx = np.repeat(np.arange(seg.size), parts)
    starts = np.repeat(np.cumsum(parts) - parts, parts)
    frac = (np.arange(parts.sum()) - starts) / np.repeat(parts, parts)

    dense_lat = np.append(lat[seg_idx] + (lat[seg_idx + 1] - lat[seg_idx]) * frac, lat[-1])
    dense_lon = np.append(lon[seg_idx] + (lon[seg_idx + 1] - lon[seg_idx]) * frac, lon[-1])
    dense_miles = np.append(cum[seg_idx] + seg[seg_idx] * frac, cum[-1])

    if total_miles and cum[-1] > 0:
        dense_miles = dense_miles * (total_miles / cum[-1])
    return DenseLine(dense_lat, dense_lon, dense_miles)


def simplify(lat: np.ndarray, lon: np.ndarray, tolerance_deg: float) -> np.ndarray:
    """Douglas-Peucker simplification. Returns the indices of points to keep.

    Used only to shrink the geometry sent to clients; station matching always
    runs on the full-resolution route.
    """
    n = len(lat)
    if n <= 2 or tolerance_deg <= 0:
        return np.arange(n)
    points = np.column_stack((lon, lat))
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        a, b = points[first], points[last]
        segment = points[first + 1:last]
        ab = b - a
        length = np.hypot(*ab)
        if length == 0:
            dist = np.hypot(*(segment - a).T)
        else:
            dist = np.abs(ab[0] * (segment[:, 1] - a[1]) - ab[1] * (segment[:, 0] - a[0])) / length
        i = int(np.argmax(dist))
        if dist[i] > tolerance_deg:
            split = first + 1 + i
            keep[split] = True
            stack.append((first, split))
            stack.append((split, last))
    return np.flatnonzero(keep)


def decode_polyline(encoded: str, precision: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """Decode a Google/OSRM encoded polyline into (lat, lon) arrays.

    OSRM's polyline6 is ~5x smaller than GeoJSON for the same route, which
    noticeably cuts transfer time for cross-country routes.
    """
    factor = 10.0 ** precision
    coords: list[int] = []
    value = shift = 0
    for char in encoded:
        byte = ord(char) - 63
        value |= (byte & 0x1F) << shift
        shift += 5
        if byte < 0x20:
            coords.append(~(value >> 1) if value & 1 else value >> 1)
            value = shift = 0
    deltas = np.asarray(coords, dtype=np.int64).reshape(-1, 2)
    absolute = np.cumsum(deltas, axis=0) / factor
    return absolute[:, 0], absolute[:, 1]


# Rough bounding boxes for the 50 states + DC (contiguous US, Alaska, Hawaii).
US_BOUNDS = (
    (24.3, 49.5, -125.0, -66.8),
    (51.0, 71.6, -180.0, -129.0),
    (18.8, 22.4, -160.6, -154.6),
)


def in_usa(lat: float, lon: float) -> bool:
    return any(lo_lat <= lat <= hi_lat and lo_lon <= lon <= hi_lon for lo_lat, hi_lat, lo_lon, hi_lon in US_BOUNDS)
