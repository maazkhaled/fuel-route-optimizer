import numpy as np
from django.test import SimpleTestCase

from planner.services.geo import decode_polyline, densify, haversine_miles, in_usa, simplify
from planner.tests.utils import encode_polyline


class GeoTests(SimpleTestCase):
    def test_haversine_new_york_to_los_angeles(self):
        miles = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
        self.assertAlmostEqual(float(miles), 2445, delta=5)

    def test_densify_limits_spacing_and_keeps_endpoints(self):
        lat, lon = np.array([35.0, 35.0, 36.0]), np.array([-100.0, -99.0, -99.0])
        line = densify(lat, lon, step_miles=0.5)
        gaps = haversine_miles(line.lat[:-1], line.lon[:-1], line.lat[1:], line.lon[1:])
        self.assertLessEqual(float(gaps.max()), 0.5 + 1e-9)
        self.assertEqual((line.lat[0], line.lon[0], line.lat[-1], line.lon[-1]), (35.0, -100.0, 36.0, -99.0))
        self.assertTrue(np.all(np.diff(line.miles) > 0))

    def test_densify_scales_to_routing_distance(self):
        line = densify(np.array([35.0, 35.0]), np.array([-100.0, -99.0]), 1.0, total_miles=100.0)
        self.assertAlmostEqual(line.total_miles, 100.0)

    def test_decode_reference_polyline(self):
        # Example from Google's polyline algorithm documentation (precision 5).
        lat, lon = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        np.testing.assert_allclose(lat, [38.5, 40.7, 43.252])
        np.testing.assert_allclose(lon, [-120.2, -120.95, -126.453])

    def test_polyline_round_trip(self):
        lat, lon = np.array([41.881832, 32.776664, 25.761681]), np.array([-87.623177, -96.796988, -80.191788])
        decoded = decode_polyline(encode_polyline(lat, lon), precision=6)
        np.testing.assert_allclose(decoded[0], lat)
        np.testing.assert_allclose(decoded[1], lon)

    def test_simplify_straight_line_to_endpoints(self):
        lat = np.linspace(30, 31, 50)
        lon = np.linspace(-100, -99, 50)
        self.assertEqual(simplify(lat, lon, 1e-4).tolist(), [0, 49])

    def test_in_usa(self):
        self.assertTrue(in_usa(41.88, -87.63))  # Chicago
        self.assertTrue(in_usa(61.22, -149.90))  # Anchorage
        self.assertFalse(in_usa(51.51, -0.13))  # London
        self.assertFalse(in_usa(19.43, -99.13))  # Mexico City
