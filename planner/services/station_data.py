"""Reading the committed geocoded station CSV into the FuelStation table."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

DEFAULT_STATIONS_CSV = Path(__file__).resolve().parent.parent / "data" / "stations_geocoded.csv"


def read_station_rows(path: Path = DEFAULT_STATIONS_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [
            {
                "opis_id": int(row["opis_id"]),
                "name": row["name"],
                "address": row["address"],
                "city": row["city"],
                "state": row["state"],
                "rack_id": int(row["rack_id"]) if row["rack_id"] else None,
                "price": Decimal(row["price"]),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "geocode_source": row["geocode_source"],
                "listing_count": int(row["listing_count"]),
            }
            for row in csv.DictReader(handle)
        ]


def replace_stations(model, rows: list[dict]) -> int:
    """Swap the table contents for `rows`. `model` may be a historical model."""
    model.objects.all().delete()
    model.objects.bulk_create([model(**row) for row in rows], batch_size=1000)
    return len(rows)
