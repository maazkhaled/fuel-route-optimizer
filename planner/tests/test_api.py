"""End-to-end API tests. The routing API is mocked; everything else is real
(station data loaded by the migration, matching, optimizer, serialization)."""

import json
from pathlib import Path
from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from planner.services.geo import decode_polyline
from planner.services.stations import StationIndex
from planner.tests.utils import fake_response, osrm_payload, route_through

CHICAGO_TO_DALLAS = ("Chicago, IL", "Bloomington, IL", "St. Louis, MO", "Springfield, MO",
                     "Joplin, MO", "Tulsa, OK", "Oklahoma City, OK", "Dallas, TX")
NEW_YORK_TO_LOS_ANGELES = ("New York, NY", "Harrisburg, PA", "Pittsburgh, PA", "Columbus, OH",
                           "Indianapolis, IN", "St. Louis, MO", "Tulsa, OK", "Oklahoma City, OK",
                           "Amarillo, TX", "Albuquerque, NM", "Flagstaff, AZ", "Barstow, CA", "Los Angeles, CA")

OSRM_GET = "planner.services.routing._session.get"


class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        StationIndex.reset()
        self.url = reverse("route-plan")

    def plan(self, waypoints, start, finish, **extra):
        payload = osrm_payload(*route_through(*waypoints))
        with mock.patch(OSRM_GET, return_value=fake_response(payload)) as get:
            response = self.client.post(self.url, {"start": start, "finish": finish, **extra}, content_type="application/json")
        return response, get

    def test_cross_country_plan(self):
        response, osrm = self.plan(NEW_YORK_TO_LOS_ANGELES, "New York, NY", "Los Angeles, CA")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()

        self.assertEqual(osrm.call_count, 1)
        self.assertEqual(body["meta"]["external_api_calls"], {"routing": 1, "geocoding": 0, "total": 1})
        self.assertGreater(body["route"]["distance_miles"], 2400)
        self.assertEqual(body["route"]["geometry"]["type"], "LineString")
        self.assertIn("map_url", body)

        stops = body["fuel_stops"]
        self.assertGreaterEqual(len(stops), 4)  # 2,500+ miles on a 500-mile range
        self.assertAlmostEqual(body["summary"]["total_fuel_cost"], sum(s["cost"] for s in stops), delta=0.05)

        # Never more than one tank between refuels, starting full.
        previous = 0.0
        for stop in stops:
            self.assertLessEqual(stop["mile_marker"] - previous, 500.5)
            self.assertLessEqual(stop["fuel_on_arrival_gallons"] + stop["gallons"], 50.01)
            previous = stop["mile_marker"]
        self.assertLessEqual(body["route"]["distance_miles"] - previous, 500.5)

        # Fuel bought + starting tank covers the whole trip.
        summary = body["summary"]
        self.assertAlmostEqual(
            summary["starting_fuel_gallons"] + summary["gallons_purchased"] - summary["fuel_at_finish_gallons"],
            summary["gallons_used"], delta=0.05,
        )
        self.assertLessEqual(summary["fuel_only_optimum"]["total_fuel_cost"], summary["total_fuel_cost"] + 1e-6)

    def test_real_osrm_geometry(self):
        """Chicago -> Dallas geometry captured from the live OSRM server (downsampled)."""
        fixture = json.loads((Path(__file__).parent / "fixtures" / "osrm_chicago_dallas.json").read_text())
        lat, lon = decode_polyline(fixture["polyline5"], precision=5)
        payload = osrm_payload(lat, lon, distance_miles=fixture["distance"] / 1609.344,
                               duration_hours=fixture["duration"] / 3600)
        with mock.patch(OSRM_GET, return_value=fake_response(payload)):
            response = self.client.post(self.url, {"start": "Chicago, IL", "finish": "Dallas, TX"},
                                        content_type="application/json")
        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        self.assertAlmostEqual(body["route"]["distance_miles"], 966.9, delta=0.1)
        self.assertGreater(body["summary"]["stations_along_route"], 100)
        self.assertEqual(body["summary"]["fuel_stop_count"], 2)
        for stop in body["fuel_stops"]:
            self.assertLess(stop["off_route_miles"], 10)
            self.assertRegex(stop["station"]["address"], r"I-\d+")  # interstate exits on the actual route

    def test_repeat_request_hits_cache(self):
        self.plan(CHICAGO_TO_DALLAS, "Chicago, IL", "Dallas, TX")
        response, osrm = self.plan(CHICAGO_TO_DALLAS, "Chicago, IL", "Dallas, TX")
        self.assertEqual(osrm.call_count, 0)
        self.assertEqual(response.json()["meta"]["external_api_calls"]["total"], 0)

    def test_get_with_query_params_and_coordinates(self):
        payload = osrm_payload(*route_through(*CHICAGO_TO_DALLAS))
        with mock.patch(OSRM_GET, return_value=fake_response(payload)):
            response = self.client.get(self.url, {"start": "41.8818,-87.6232", "finish": "Dallas, TX", "geometry": "none"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("geometry", response.json()["route"])
        self.assertEqual(response.json()["start"]["resolved_by"], "coordinates")

    def test_short_trip_needs_no_fuel(self):
        response, _ = self.plan(("Dallas, TX", "Waco, TX", "Austin, TX"), "Dallas, TX", "Austin, TX")
        body = response.json()
        self.assertEqual(body["fuel_stops"], [])
        self.assertEqual(body["summary"]["total_fuel_cost"], 0)
        self.assertGreater(body["summary"]["gallons_used"], 15)

    def test_include_stations_and_zero_penalty(self):
        response, _ = self.plan(CHICAGO_TO_DALLAS, "Chicago, IL", "Dallas, TX", include_stations=True, stop_penalty=0)
        body = response.json()
        self.assertEqual(len(body["stations_along_route"]), body["summary"]["stations_along_route"])
        self.assertEqual(body["summary"]["total_fuel_cost"], body["summary"]["fuel_only_optimum"]["total_fuel_cost"])

    def test_invalid_body(self):
        response = self.client.post(self.url, {"start": ""}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_outside_usa(self):
        response = self.client.post(self.url, {"start": "51.5,-0.12", "finish": "Dallas, TX"}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "outside_usa")

    def test_same_start_and_finish(self):
        response = self.client.post(self.url, {"start": "Dallas, TX", "finish": "dallas, texas"}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "same_location")

    def test_no_route(self):
        no_route = fake_response({"code": "NoRoute", "message": "Impossible route"}, status=400)
        with mock.patch(OSRM_GET, return_value=no_route):
            response = self.client.post(self.url, {"start": "Honolulu, HI", "finish": "Dallas, TX"}, content_type="application/json")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "no_route")

    def test_routing_service_down(self):
        with mock.patch(OSRM_GET, side_effect=requests.ConnectionError("down")):
            response = self.client.post(self.url, {"start": "Chicago, IL", "finish": "Dallas, TX"}, content_type="application/json")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "routing_unavailable")

    @override_settings(FUEL_PLANNER={
        "VEHICLE_RANGE_MILES": 500, "VEHICLE_MPG": 10, "START_FUEL": "full", "CORRIDOR_MILES": 0.01,
        "DENSIFY_STEP_MILES": 0.5, "GEOMETRY_SIMPLIFY_TOLERANCE": 0.0005, "STOP_PENALTY_USD": 5,
    })
    def test_no_station_within_range(self):
        # Stations sit at town centres; shift the route ~3.5 miles north of them
        # and shrink the corridor so nothing qualifies.
        lat, lon = route_through(*NEW_YORK_TO_LOS_ANGELES)
        with mock.patch(OSRM_GET, return_value=fake_response(osrm_payload(lat + 0.05, lon))):
            response = self.client.post(
                self.url, {"start": "New York, NY", "finish": "Los Angeles, CA"}, content_type="application/json"
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "no_fuel_within_range")

    def test_map_view_renders_plan(self):
        payload = osrm_payload(*route_through(*CHICAGO_TO_DALLAS))
        with mock.patch(OSRM_GET, return_value=fake_response(payload)):
            response = self.client.get(reverse("route-map"), {"start": "Chicago, IL", "finish": "Dallas, TX"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total spent on fuel")
        self.assertContains(response, "plan-data")

    def test_map_view_shows_errors(self):
        response = self.client.get(reverse("route-map"), {"start": "51.5,-0.12", "finish": "Dallas, TX"})
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "outside the USA", status_code=422)

    def test_health(self):
        body = self.client.get(reverse("health")).json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["stations_loaded"], 6626)
