from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase

from planner.services.calls import ExternalCallCounter
from planner.services.locations import (
    GeocoderUnavailable,
    LocationNotFound,
    LocationOutsideUSA,
    resolve_location,
)
from planner.tests.utils import fake_response


class ResolveLocationTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.calls = ExternalCallCounter()

    def test_city_state_resolves_locally(self):
        for text in ("Chicago, IL", "chicago, illinois", "Chicago, IL 60601", "Chicago, IL, USA"):
            location = resolve_location(text, self.calls)
            self.assertEqual(location.resolved_by, "place_table", text)
            self.assertAlmostEqual(location.latitude, 41.88, delta=0.1)
        self.assertEqual(self.calls.total, 0)

    def test_abbreviated_saint_and_fort(self):
        self.assertEqual(resolve_location("St. Louis, MO", self.calls).label, "St. Louis, MO")
        self.assertEqual(resolve_location("Ft Worth, TX", self.calls).resolved_by, "place_table")

    def test_coordinates_as_text_and_object(self):
        self.assertEqual(resolve_location("41.88, -87.63", self.calls).resolved_by, "coordinates")
        self.assertEqual(resolve_location({"lat": 41.88, "lon": -87.63}, self.calls).resolved_by, "coordinates")
        self.assertEqual(resolve_location({"latitude": 41.88, "lng": -87.63}, self.calls).resolved_by, "coordinates")
        self.assertEqual(self.calls.total, 0)

    def test_rejects_locations_outside_usa(self):
        with self.assertRaises(LocationOutsideUSA):
            resolve_location("51.5074,-0.1278", self.calls)

    def test_free_text_uses_geocoder_once_then_cache(self):
        payload = [{"lat": "38.8977", "lon": "-77.0365", "display_name": "White House, Washington, DC"}]
        with mock.patch("planner.services.locations.requests.get", return_value=fake_response(payload)) as get:
            first = resolve_location("1600 Pennsylvania Ave NW, Washington DC", self.calls)
            second = resolve_location("1600 Pennsylvania Ave NW, Washington DC", self.calls)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(self.calls.geocoding, 1)
        self.assertEqual(first, second)
        self.assertEqual(first.resolved_by, "nominatim")

    def test_unknown_place(self):
        with mock.patch("planner.services.locations.requests.get", return_value=fake_response([])):
            with self.assertRaises(LocationNotFound):
                resolve_location("Nowhereville Zzz", self.calls)

    def test_geocoder_down(self):
        with mock.patch("planner.services.locations.requests.get", side_effect=requests.ConnectionError):
            with self.assertRaises(GeocoderUnavailable):
                resolve_location("Somewhere Unlisted", self.calls)
