"""
Orchestrates one trip plan:

  resolve start/finish  ->  1 routing call  ->  match stations to the route
  (local)                   (cached)            (local, in-memory KD-tree)
                                                        |
                         response  <-  cheapest-fuel optimizer (local)
"""

from __future__ import annotations

from time import perf_counter

from django.conf import settings

from .calls import ExternalCallCounter
from .geo import densify, haversine_miles, simplify
from .locations import LocationNotFound, resolve_location
from .optimizer import Vehicle, plan_fuel_stops, plan_fuel_stops_practical
from .routing import get_route
from .stations import StationIndex

GEOMETRY_CHOICES = ("simplified", "full", "none")


class SameStartAndFinish(LocationNotFound):
    pass


def vehicle_from_settings() -> Vehicle:
    conf = settings.FUEL_PLANNER
    return Vehicle(range_miles=conf["VEHICLE_RANGE_MILES"], mpg=conf["VEHICLE_MPG"])


def starting_fuel(vehicle: Vehicle) -> float:
    value = str(settings.FUEL_PLANNER["START_FUEL"]).strip().lower()
    return vehicle.tank_gallons if value == "full" else min(float(value), vehicle.tank_gallons)


def plan_trip(
    start_value,
    finish_value,
    geometry: str = "simplified",
    include_candidates: bool = False,
    stop_penalty: float | None = None,
) -> dict:
    started = perf_counter()
    conf = settings.FUEL_PLANNER
    if stop_penalty is None:
        stop_penalty = conf["STOP_PENALTY_USD"]
    calls = ExternalCallCounter()
    vehicle = vehicle_from_settings()

    start = resolve_location(start_value, calls)
    finish = resolve_location(finish_value, calls)
    if haversine_miles(start.latitude, start.longitude, finish.latitude, finish.longitude) < 0.1:
        raise SameStartAndFinish("Start and finish are the same place.")

    routing_started = perf_counter()
    route = get_route((start.latitude, start.longitude), (finish.latitude, finish.longitude), calls)
    routing_ms = (perf_counter() - routing_started) * 1000

    planning_started = perf_counter()
    dense = densify(route.lat, route.lon, conf["DENSIFY_STEP_MILES"], total_miles=route.distance_miles)
    index = StationIndex.get()
    candidates = index.along_route(dense, conf["CORRIDOR_MILES"])
    miles = [c.mile for c in candidates]
    prices = [c.price for c in candidates]
    start_fuel = starting_fuel(vehicle)
    plan = plan_fuel_stops_practical(miles, prices, dense.total_miles, vehicle, start_fuel, stop_penalty)
    # The absolute cheapest plan, ignoring how many stops it takes, for comparison.
    cheapest = plan_fuel_stops(miles, prices, dense.total_miles, vehicle, start_fuel)
    planning_ms = (perf_counter() - planning_started) * 1000

    stops = []
    previous_mile = 0.0
    for number, purchase in enumerate(plan.purchases, start=1):
        candidate = candidates[purchase.index]
        record = index.records[candidate.station]
        stops.append({
            "stop_number": number,
            "station": {
                "opis_id": record["opis_id"],
                "name": record["name"],
                "address": record["address"],
                "city": record["city"],
                "state": record["state"],
                "lat": round(record["latitude"], 5),
                "lon": round(record["longitude"], 5),
            },
            "mile_marker": round(purchase.mile, 1),
            "miles_since_previous_fuel": round(purchase.mile - previous_mile, 1),
            "off_route_miles": round(candidate.off_route_miles, 1),
            "price_per_gallon": round(purchase.price, 3),
            "fuel_on_arrival_gallons": round(purchase.fuel_on_arrival, 2),
            "gallons": round(purchase.gallons, 2),
            "cost": round(purchase.cost, 2),
        })
        previous_mile = purchase.mile

    response = {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": {
            "provider": route.provider,
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_seconds / 3600, 2),
        },
        "fuel_stops": stops,
        "summary": {
            "total_fuel_cost": round(plan.total_cost, 2),
            "currency": "USD",
            "fuel_stop_count": len(stops),
            "gallons_purchased": round(plan.gallons_purchased, 2),
            "gallons_used": round(plan.gallons_used, 2),
            "starting_fuel_gallons": round(plan.start_fuel, 2),
            "fuel_at_finish_gallons": round(plan.end_fuel, 2),
            "average_price_paid": (
                round(plan.total_cost / plan.gallons_purchased, 3) if plan.gallons_purchased else None
            ),
            "stations_along_route": len(candidates),
            "stop_penalty_usd": stop_penalty,
            "fuel_only_optimum": {
                "total_fuel_cost": round(cheapest.total_cost, 2),
                "fuel_stop_count": len(cheapest.purchases),
                "note": "Cheapest possible fuel bill if extra stops cost nothing.",
            },
        },
        "assumptions": {
            "vehicle_range_miles": vehicle.range_miles,
            "miles_per_gallon": vehicle.mpg,
            "tank_gallons": vehicle.tank_gallons,
            "starts_with": "full tank" if plan.start_fuel == vehicle.tank_gallons else f"{plan.start_fuel} gallons",
            "total_fuel_cost_means": "money spent at fuel stops during this trip",
            "optimises": "fuel cost + stop_penalty_usd per stop (avoids stopping for a few cents)",
            "corridor_miles": conf["CORRIDOR_MILES"],
            "station_location_precision": "town-level (source data has no coordinates)",
        },
    }

    if geometry != "none":
        keep = (
            simplify(route.lat, route.lon, conf["GEOMETRY_SIMPLIFY_TOLERANCE"])
            if geometry == "simplified" else range(route.lat.size)
        )
        response["route"]["geometry"] = {
            "type": "LineString",
            "coordinates": [[round(float(route.lon[i]), 5), round(float(route.lat[i]), 5)] for i in keep],
        }

    if include_candidates:
        response["stations_along_route"] = [
            {
                "name": index.records[c.station]["name"],
                "city": index.records[c.station]["city"],
                "state": index.records[c.station]["state"],
                "lat": round(float(index.lat[c.station]), 5),
                "lon": round(float(index.lon[c.station]), 5),
                "price_per_gallon": round(c.price, 3),
                "mile_marker": round(c.mile, 1),
            }
            for c in candidates
        ]

    response["meta"] = {
        "external_api_calls": {"routing": calls.routing, "geocoding": calls.geocoding, "total": calls.total},
        "timings_ms": {
            "routing": round(routing_ms, 1),
            "planning": round(planning_ms, 1),
            "total": round((perf_counter() - started) * 1000, 1),
        },
    }
    return response
