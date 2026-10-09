"""
Offline data preparation: raw fuel price CSV -> geocoded station table.

The raw file has no coordinates, and its addresses are highway-exit
descriptions ("I-44, EXIT 283 & US-69") that geocoders cannot resolve. Every
row does have a town and state, so each station is placed at its town's
coordinates using two free place datasets:

  1. GeoNames US places (CC BY 4.0)         - city-centre points, ~17k towns
  2. kelvins/US-Cities-Database (MIT)        - ~30k places incl. small towns
  3. place_aliases.csv                       - a handful of manual aliases
  4. Nominatim (optional, --nominatim)       - anything still unresolved

This runs once. Its outputs are committed, so the API never geocodes stations
at request time:

  planner/data/us_places.csv            one point per "CITY|ST" (also used to
                                        resolve user input like "Dallas, TX")
  planner/data/stations_geocoded.csv    one row per station, with lat/lon
"""

from __future__ import annotations

import csv
import math
import time
from collections import defaultdict
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from planner.services.places import (
    DATA_DIR,
    PLACES_CSV,
    US_STATE_CODES,
    Place,
    PlaceIndex,
    normalize_place,
    place_key,
)

RAW_CSV = DATA_DIR / "raw" / "fuel-prices-for-be-assessment.csv"
STATIONS_CSV = DATA_DIR / "stations_geocoded.csv"
SOURCES = DATA_DIR / "sources"

STATION_FIELDS = [
    "opis_id", "name", "address", "city", "state", "rack_id", "price",
    "latitude", "longitude", "geocode_source", "listing_count",
]


def _distance_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], (a[1] - b[1]) * math.cos(math.radians(a[0])))


def build_places() -> dict[str, Place]:
    """Merge the place datasets into one point per CITY|ST key."""
    geonames: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    with open(SOURCES / "geonames_us_places.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["state"] in US_STATE_CODES:
                geonames[place_key(row["name"], row["state"])].append(
                    (row["name"], float(row["latitude"]), float(row["longitude"]))
                )

    kelvins: dict[str, list[tuple[str, str, float, float]]] = defaultdict(list)
    with open(SOURCES / "us_cities_kelvins.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["STATE_CODE"] in US_STATE_CODES:
                kelvins[place_key(row["CITY"], row["STATE_CODE"])].append(
                    (row["CITY"], row["COUNTY"], float(row["LATITUDE"]), float(row["LONGITUDE"]))
                )

    places: dict[str, Place] = {}
    for key in set(geonames) | set(kelvins):
        state = key.split("|")[1]
        gn, kv = geonames.get(key, []), kelvins.get(key, [])
        if len(gn) == 1:
            name, lat, lon = gn[0]
            places[key] = Place(name, state, lat, lon, "geonames")
        elif len(gn) > 1:
            # Same name twice in one state (e.g. two Albany, MO). Use the second
            # dataset to pick the right one when it can; otherwise flag it.
            if kv:
                anchor = (kv[0][2], kv[0][3])
                name, lat, lon = min(gn, key=lambda g: _distance_deg((g[1], g[2]), anchor))
                places[key] = Place(name, state, lat, lon, "geonames")
            else:
                name, lat, lon = gn[0]
                places[key] = Place(name, state, lat, lon, "geonames_ambiguous")
        else:
            # kelvins can list a city once per county; prefer the county that
            # shares the city's name (Dallas in Dallas County, not Collin).
            city_norm = normalize_place(kv[0][0])
            pick = next((k for k in kv if normalize_place(k[1]) == city_norm), kv[0])
            places[key] = Place(pick[0], state, pick[2], pick[3], "us_cities")

    with open(SOURCES / "place_aliases.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            target = places.get(place_key(row["alias_city"], row["alias_state"]))
            if target:
                places[place_key(row["city"], row["state"])] = Place(
                    row["city"], row["state"], target.latitude, target.longitude, "alias"
                )
    return places


def write_places(places: dict[str, Place], path: Path = PLACES_CSV) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["key", "name", "state", "latitude", "longitude", "source"])
        for key in sorted(places):
            p = places[key]
            writer.writerow([key, p.name, p.state, f"{p.latitude:.5f}", f"{p.longitude:.5f}", p.source])


def read_raw_stations(path: Path, duplicate_price: str) -> tuple[list[dict], dict[str, int]]:
    """Read the raw price file, keep US rows, collapse duplicate station IDs."""
    stats = {"rows": 0, "non_us_rows": 0, "duplicate_rows": 0}
    listings: dict[int, list[dict]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            stats["rows"] += 1
            state = row["State"].strip().upper()
            if state not in US_STATE_CODES:
                stats["non_us_rows"] += 1
                continue
            listings[int(row["OPIS Truckstop ID"])].append({
                "name": " ".join(row["Truckstop Name"].split()),
                "address": " ".join(row["Address"].split()),
                "city": " ".join(row["City"].split()),
                "state": state,
                "rack_id": row["Rack ID"].strip(),
                "price": float(row["Retail Price"]),
            })

    stations = []
    for opis_id, rows in listings.items():
        stats["duplicate_rows"] += len(rows) - 1
        prices = [r["price"] for r in rows]
        if duplicate_price == "min":
            chosen = min(rows, key=lambda r: r["price"])
            price = chosen["price"]
        elif duplicate_price == "max":
            chosen = max(rows, key=lambda r: r["price"])
            price = chosen["price"]
        else:
            chosen = rows[0]
            price = sum(prices) / len(prices)
        stations.append({**chosen, "opis_id": opis_id, "price": price, "listing_count": len(rows)})
    stations.sort(key=lambda s: s["opis_id"])
    return stations, stats


class NominatimFallback:
    """Rate-limited (1 req/s, per Nominatim policy) geocoder with a disk cache."""

    cache_path = SOURCES / "nominatim_cache.csv"

    def __init__(self):
        self.cache: dict[str, tuple[float, float] | None] = {}
        if self.cache_path.exists():
            with open(self.cache_path, newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    self.cache[row["key"]] = (
                        (float(row["latitude"]), float(row["longitude"])) if row["latitude"] else None
                    )
        self._last_call = 0.0

    def lookup(self, city: str, state: str) -> tuple[float, float] | None:
        key = place_key(city, state)
        if key not in self.cache:
            wait = 1.05 - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            response = requests.get(
                settings.GEOCODING["NOMINATIM_URL"],
                params={"city": city, "state": state, "country": "USA", "format": "jsonv2", "limit": 1},
                headers={"User-Agent": settings.GEOCODING["USER_AGENT"]},
                timeout=settings.GEOCODING["TIMEOUT_SECONDS"],
            )
            self._last_call = time.monotonic()
            response.raise_for_status()
            results = response.json()
            self.cache[key] = (float(results[0]["lat"]), float(results[0]["lon"])) if results else None
            self._save()
        return self.cache[key]

    def _save(self) -> None:
        with open(self.cache_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["key", "latitude", "longitude"])
            for key, point in sorted(self.cache.items()):
                writer.writerow([key, *(point if point else ("", ""))])


class Command(BaseCommand):
    help = "Geocode the raw fuel price CSV into planner/data/stations_geocoded.csv."

    def add_arguments(self, parser):
        parser.add_argument("--input", type=Path, default=RAW_CSV, help="Raw fuel price CSV.")
        parser.add_argument("--output", type=Path, default=STATIONS_CSV, help="Geocoded output CSV.")
        parser.add_argument(
            "--duplicate-price", choices=["min", "mean", "max"], default="min",
            help="How to price a station listed more than once (default: its lowest listed price).",
        )
        parser.add_argument(
            "--nominatim", action="store_true",
            help="Geocode towns missing from the place tables via Nominatim (1 request/second).",
        )
        parser.add_argument("--load", action="store_true", help="Reload the database table afterwards.")

    def handle(self, *args, **options):
        if not options["input"].exists():
            raise CommandError(f"Input file not found: {options['input']}")

        places = build_places()
        write_places(places)
        PlaceIndex._instance = PlaceIndex(places)
        index = PlaceIndex._instance
        self.stdout.write(f"Place table: {len(places):,} US places -> {PLACES_CSV.relative_to(DATA_DIR.parent.parent)}")

        stations, stats = read_raw_stations(options["input"], options["duplicate_price"])
        fallback = NominatimFallback() if options["nominatim"] else None
        sources: dict[str, int] = defaultdict(int)
        unresolved: list[dict] = []
        output = []
        for station in stations:
            place = index.lookup(station["city"], station["state"])
            if place:
                lat, lon, source = place.latitude, place.longitude, place.source
            elif fallback and (point := fallback.lookup(station["city"], station["state"])):
                (lat, lon), source = point, "nominatim"
            else:
                unresolved.append(station)
                continue
            sources[source] += 1
            output.append({
                **station,
                "price": f"{station['price']:.6f}",
                "latitude": f"{lat:.5f}",
                "longitude": f"{lon:.5f}",
                "geocode_source": source,
            })

        with open(options["output"], "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=STATION_FIELDS)
            writer.writeheader()
            for row in output:
                writer.writerow({field: row[field] for field in STATION_FIELDS})

        self.stdout.write(
            f"Raw rows: {stats['rows']:,} | non-US rows dropped: {stats['non_us_rows']:,} | "
            f"duplicate listings merged: {stats['duplicate_rows']:,} (price rule: {options['duplicate_price']})"
        )
        self.stdout.write(
            f"Stations geocoded: {len(output):,} of {len(stations):,} "
            f"({len(output) / max(len(stations), 1):.1%}) | by source: {dict(sorted(sources.items()))}"
        )
        if unresolved:
            towns = sorted({f"{s['city']}, {s['state']}" for s in unresolved})
            self.stdout.write(self.style.WARNING(
                f"Unresolved: {len(unresolved)} stations in {len(towns)} towns: {', '.join(towns[:20])}"
                + (" (re-run with --nominatim to resolve)" if not fallback else "")
            ))
        self.stdout.write(self.style.SUCCESS(f"Wrote {options['output']}"))

        if options["load"]:
            from django.core.management import call_command
            call_command("load_stations", path=str(options["output"]))
