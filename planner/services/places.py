"""
US place-name handling shared by offline data prep and request-time lookups.

The fuel price file only gives each station's town and state, so stations are
geocoded by joining "CITY|ST" against a table of US places. The same table
resolves user input like "Chicago, IL" locally, without an external API call.
"""

from __future__ import annotations

import csv
import re
import threading
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLACES_CSV = DATA_DIR / "us_places.csv"

STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "WASHINGTON DC": "DC", "DC": "DC",
}
US_STATE_CODES = frozenset(STATE_NAMES.values())

_PREFIXES = (
    (re.compile(r"^ST\s"), "SAINT "),
    (re.compile(r"^STE\s"), "SAINTE "),
    (re.compile(r"^FT\s"), "FORT "),
    (re.compile(r"^MT\s"), "MOUNT "),
    (re.compile(r"^PT\s"), "PORT "),
)


def normalize_place(name: str) -> str:
    """Canonical form for matching place names: 'St. Louis ' -> 'SAINT LOUIS'."""
    text = str(name or "").upper().strip()
    text = re.sub(r"[.'`’]", "", text)
    text = re.sub(r"[-/]", " ", text)
    text = re.sub(r"\s+", " ", text)
    for pattern, replacement in _PREFIXES:
        text = pattern.sub(replacement, text)
    return text


def normalize_state(state: str) -> str | None:
    """'TX', 'tx', 'Texas' -> 'TX'. Returns None for anything not a US state/DC."""
    text = re.sub(r"[.\s]+", " ", str(state or "").upper()).strip()
    if text in US_STATE_CODES:
        return text
    return STATE_NAMES.get(text)


def place_key(city: str, state: str) -> str:
    return f"{normalize_place(city)}|{state}"


def compact_key(key: str) -> str:
    """Space-insensitive variant so 'BROOKPARK|OH' matches 'BROOK PARK|OH'."""
    return key.replace(" ", "")


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    latitude: float
    longitude: float
    source: str

    @property
    def label(self) -> str:
        return f"{self.name}, {self.state}"


class PlaceIndex:
    """In-memory lookup of ~30k US places, loaded once per process."""

    _instance: "PlaceIndex | None" = None
    _lock = threading.Lock()

    def __init__(self, places: dict[str, Place]):
        self._by_key = places
        self._by_compact = {compact_key(key): place for key, place in places.items()}

    @classmethod
    def from_csv(cls, path: Path = PLACES_CSV) -> "PlaceIndex":
        places: dict[str, Place] = {}
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                places[row["key"]] = Place(
                    name=row["name"],
                    state=row["state"],
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    source=row["source"],
                )
        return cls(places)

    @classmethod
    def get(cls) -> "PlaceIndex":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls.from_csv()
        return cls._instance

    def lookup(self, city: str, state: str) -> Place | None:
        code = normalize_state(state)
        if code is None:
            return None
        key = place_key(city, code)
        return self._by_key.get(key) or self._by_compact.get(compact_key(key))

    def __len__(self) -> int:
        return len(self._by_key)
