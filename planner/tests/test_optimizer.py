import itertools
import random

from django.test import SimpleTestCase
from scipy.optimize import linprog

from planner.services.optimizer import (
    UnreachableSegmentError,
    Vehicle,
    next_cheaper_indices,
    plan_fuel_stops,
    plan_fuel_stops_practical,
)

VEHICLE = Vehicle(range_miles=500, mpg=10)


def lp_optimum(miles, prices, total, start_fuel):
    """Exact minimum fuel cost via linear programming (None if infeasible)."""
    n, tank, mpg = len(miles), VEHICLE.tank_gallons, VEHICLE.mpg
    stops = list(miles) + [total]
    a_ub, b_ub = [], []
    for k in range(n + 1):  # never arrive anywhere with a negative tank
        a_ub.append([-1.0 if i < k else 0.0 for i in range(n)])
        b_ub.append(start_fuel - stops[k] / mpg)
    for k in range(n):  # never exceed the tank after buying
        a_ub.append([1.0 if i <= k else 0.0 for i in range(n)])
        b_ub.append(tank - start_fuel + stops[k] / mpg)
    if n == 0:
        return 0.0 if start_fuel * mpg >= total else None
    result = linprog(prices, A_ub=a_ub, b_ub=b_ub, bounds=[(0, None)] * n, method="highs")
    return result.fun if result.status == 0 else None


def random_instance(rng, max_stations):
    n = rng.randint(1, max_stations)
    total = rng.uniform(50, 2500)
    miles = sorted(int(rng.uniform(0, total) * 10) / 10 for _ in range(n))
    prices = [round(rng.uniform(2.6, 4.2), 3) for _ in range(n)]
    start = rng.choice([VEHICLE.tank_gallons, rng.uniform(0, VEHICLE.tank_gallons)])
    return miles, prices, total, start


def assert_physically_valid(test, plan, start_fuel):
    fuel, position = start_fuel, 0.0
    for purchase in plan.purchases:
        fuel -= (purchase.mile - position) / VEHICLE.mpg
        position = purchase.mile
        test.assertGreaterEqual(fuel, -1e-6, "ran out of fuel before a stop")
        fuel += purchase.gallons
        test.assertLessEqual(fuel, VEHICLE.tank_gallons + 1e-6, "overfilled the tank")
    fuel -= (plan.total_miles - position) / VEHICLE.mpg
    test.assertGreaterEqual(fuel, -1e-6, "ran out of fuel before the finish")


class CheapestPlanTests(SimpleTestCase):
    def test_buys_just_enough_to_reach_a_cheaper_station(self):
        # Empty at A ($3.50, mile 0); B is cheaper ($3.00, mile 300); trip is 700 miles.
        plan = plan_fuel_stops([0, 300], [3.50, 3.00], 700, VEHICLE, start_fuel=0)
        self.assertEqual([(p.mile, round(p.gallons, 6)) for p in plan.purchases], [(0, 30.0), (300, 40.0)])
        self.assertAlmostEqual(plan.total_cost, 225.0)

    def test_fills_up_at_the_cheapest_station_in_range(self):
        # C is cheapest in range, so skip both pricier stations and buy everything at C.
        plan = plan_fuel_stops([0, 100, 200], [3.00, 3.50, 3.20], 450, VEHICLE, start_fuel=0)
        self.assertEqual(len(plan.purchases), 1)
        self.assertAlmostEqual(plan.purchases[0].gallons, 45.0)
        self.assertAlmostEqual(plan.total_cost, 135.0)

    def test_full_tank_covers_short_trip(self):
        plan = plan_fuel_stops([50, 100], [3.0, 3.1], 480, VEHICLE)
        self.assertEqual(plan.purchases, [])
        self.assertEqual(plan.total_cost, 0)
        self.assertAlmostEqual(plan.gallons_used, 48.0)

    def test_does_not_buy_more_than_needed_to_finish(self):
        plan = plan_fuel_stops([400], [3.0], 700, VEHICLE)
        self.assertAlmostEqual(plan.gallons_purchased, 20.0)  # arrives with 10 gal, needs 30 more
        self.assertAlmostEqual(plan.end_fuel, 0.0)

    def test_gap_longer_than_range_is_reported(self):
        with self.assertRaises(UnreachableSegmentError) as ctx:
            plan_fuel_stops([300, 900], [3.0, 3.0], 1000, VEHICLE)
        self.assertEqual((ctx.exception.from_mile, ctx.exception.to_mile), (300, 900))

    def test_next_cheaper_indices(self):
        self.assertEqual(next_cheaper_indices([3, 5, 4, 2, 6]), [3, 2, 3, 5, 5])

    def test_matches_linear_programming_optimum(self):
        rng = random.Random(42)
        checked = 0
        for _ in range(600):
            miles, prices, total, start = random_instance(rng, 20)
            expected = lp_optimum(miles, prices, total, start)
            if expected is None:
                with self.assertRaises(UnreachableSegmentError):
                    plan_fuel_stops(miles, prices, total, VEHICLE, start)
                continue
            plan = plan_fuel_stops(miles, prices, total, VEHICLE, start)
            self.assertAlmostEqual(plan.total_cost, expected, places=6)
            assert_physically_valid(self, plan, start)
            checked += 1
        self.assertGreater(checked, 250)


class PracticalPlanTests(SimpleTestCase):
    def test_zero_penalty_is_the_cheapest_plan(self):
        rng = random.Random(3)
        for _ in range(100):
            miles, prices, total, start = random_instance(rng, 15)
            try:
                cheapest = plan_fuel_stops(miles, prices, total, VEHICLE, start)
            except UnreachableSegmentError:
                continue
            practical = plan_fuel_stops_practical(miles, prices, total, VEHICLE, start, stop_penalty=0)
            self.assertAlmostEqual(practical.total_cost, cheapest.total_cost, places=6)

    def test_avoids_stops_that_save_only_cents(self):
        # Prices drop by a cent every 20 miles: the pure optimum stops at every station.
        miles = [100 + 20 * i for i in range(30)]
        prices = [3.50 - 0.01 * i for i in range(30)]
        cheapest = plan_fuel_stops(miles, prices, 1100, VEHICLE)
        practical = plan_fuel_stops_practical(miles, prices, 1100, VEHICLE, stop_penalty=5)
        self.assertGreaterEqual(len(cheapest.purchases), 8)
        self.assertLessEqual(len(practical.purchases), 3)
        self.assertLess(practical.total_cost - cheapest.total_cost, 5 * (len(cheapest.purchases) - len(practical.purchases)))

    def test_matches_brute_force_over_all_stop_sets(self):
        """For small instances, try every subset of stations as the allowed stops."""
        rng = random.Random(11)
        checked = 0
        for _ in range(150):
            miles, prices, total, start = random_instance(rng, 8)
            penalty = rng.choice([1e-9, 2.0, 10.0, 25.0])
            best = None
            for size in range(len(miles) + 1):
                for subset in itertools.combinations(range(len(miles)), size):
                    try:
                        plan = plan_fuel_stops([miles[i] for i in subset], [prices[i] for i in subset], total, VEHICLE, start)
                    except UnreachableSegmentError:
                        continue
                    value = plan.total_cost + penalty * len(plan.purchases)
                    best = value if best is None else min(best, value)
            if best is None:
                with self.assertRaises(UnreachableSegmentError):
                    plan_fuel_stops_practical(miles, prices, total, VEHICLE, start, penalty)
                continue
            plan = plan_fuel_stops_practical(miles, prices, total, VEHICLE, start, penalty)
            self.assertAlmostEqual(plan.total_cost + penalty * len(plan.purchases), best, places=6)
            assert_physically_valid(self, plan, start)
            checked += 1
        self.assertGreater(checked, 40)
