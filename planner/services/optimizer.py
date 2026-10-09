"""
Cheapest-fuel planning along a fixed route.

The problem
-----------
Stations sit at known mile markers along the route, each with a price per
gallon. The tank holds `range / mpg` gallons. The trip burns a fixed number of
gallons (distance / mpg), so minimising cost is purely a question of *where*
those gallons are bought and *how many* at each stop.

The rule (optimal for this problem)
-----------------------------------
Treat the destination as a station selling fuel for $0. At each station, in
order along the route:

1. Find the first station ahead that is strictly cheaper than this one.
2. If it is within range, buy only enough to reach it (possibly nothing, if
   the tank already covers it). Every gallon burned before that point is
   cheapest here, and every gallon after it is cheaper there.
3. If it is out of range, this is the cheapest fuel reachable on a full tank,
   so fill the tank here.

"First cheaper station" for every station comes from one pass with a monotonic
stack, so the whole plan is O(n) in the number of candidate stations.

The tests check this rule against a linear-programming solver on random
instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

EPS_MILES = 1e-6
EPS_GALLONS = 1e-6


@dataclass(frozen=True)
class Vehicle:
    range_miles: float = 500.0
    mpg: float = 10.0

    @property
    def tank_gallons(self) -> float:
        return self.range_miles / self.mpg


@dataclass(frozen=True)
class Purchase:
    index: int  # position in the input sequences
    mile: float
    price: float
    gallons: float
    fuel_on_arrival: float

    @property
    def cost(self) -> float:
        return self.gallons * self.price


@dataclass
class FuelPlan:
    purchases: list[Purchase]
    total_miles: float
    start_fuel: float
    end_fuel: float
    vehicle: Vehicle
    gallons_used: float = field(init=False)

    def __post_init__(self):
        self.gallons_used = self.total_miles / self.vehicle.mpg

    @property
    def total_cost(self) -> float:
        return sum(p.cost for p in self.purchases)

    @property
    def gallons_purchased(self) -> float:
        return sum(p.gallons for p in self.purchases)


class UnreachableSegmentError(Exception):
    """Two consecutive refuelling points are further apart than the vehicle's range."""

    def __init__(self, from_mile: float, to_mile: float, range_miles: float):
        self.from_mile = from_mile
        self.to_mile = to_mile
        self.range_miles = range_miles
        super().__init__(
            f"No fuel station within {range_miles:.0f} miles between mile {from_mile:.0f} "
            f"and mile {to_mile:.0f} of the route."
        )


def next_cheaper_indices(prices: Sequence[float]) -> list[int]:
    """For each i, the smallest j > i with prices[j] < prices[i] (len(prices) if none)."""
    n = len(prices)
    result = [n] * n
    stack: list[int] = []
    for i in range(n - 1, -1, -1):
        while stack and prices[stack[-1]] >= prices[i]:
            stack.pop()
        if stack:
            result[i] = stack[-1]
        stack.append(i)
    return result


def plan_fuel_stops(
    miles: Sequence[float],
    prices: Sequence[float],
    total_miles: float,
    vehicle: Vehicle = Vehicle(),
    start_fuel: float | None = None,
) -> FuelPlan:
    """Plan the cheapest purchases for a route.

    Args:
        miles: mile marker of each candidate station, sorted ascending.
        prices: price per gallon at each station (same order).
        total_miles: route length; the destination is at this mile.
        vehicle: range and fuel economy.
        start_fuel: gallons in the tank at the start (default: full tank).

    Raises:
        UnreachableSegmentError: if some gap exceeds what the vehicle can drive.
    """
    if len(miles) != len(prices):
        raise ValueError("miles and prices must have the same length")
    if any(b < a for a, b in zip(miles, miles[1:])):
        raise ValueError("miles must be sorted ascending")
    if miles and (miles[0] < -EPS_MILES or miles[-1] > total_miles + EPS_MILES):
        raise ValueError("station miles must lie between the start (0) and the finish")

    tank = vehicle.tank_gallons
    fuel = tank if start_fuel is None else min(max(start_fuel, 0.0), tank)
    start = fuel

    # Destination as a free station: it is "cheaper" than every real one, so
    # we never buy more than needed to finish.
    stops = list(miles) + [total_miles]
    costs = list(prices) + [0.0]
    n = len(miles)

    # Feasibility: the first leg is limited by the starting fuel, every later
    # leg by a full tank.
    reach = fuel * vehicle.mpg
    previous = 0.0
    for mile in stops:
        if mile - previous > reach + EPS_MILES:
            raise UnreachableSegmentError(previous, mile, vehicle.range_miles)
        previous, reach = mile, vehicle.range_miles

    next_cheaper = next_cheaper_indices(costs)
    purchases: list[Purchase] = []
    position = 0.0
    for i in range(n):
        fuel -= (stops[i] - position) / vehicle.mpg
        fuel = max(fuel, 0.0)  # absorb float noise; feasibility was checked above
        position = stops[i]

        j = min(next_cheaper[i], n)
        distance_to_cheaper = stops[j] - stops[i]
        if distance_to_cheaper <= vehicle.range_miles + EPS_MILES:
            target = distance_to_cheaper / vehicle.mpg
        else:
            target = tank
        buy = min(max(target - fuel, 0.0), tank - fuel)
        if buy > EPS_GALLONS:
            purchases.append(Purchase(i, stops[i], costs[i], buy, fuel))
            fuel += buy

    fuel -= (total_miles - position) / vehicle.mpg
    return FuelPlan(purchases, total_miles, start, max(fuel, 0.0), vehicle)


# --- Practical plans: fuel cost + a cost per stop -----------------------------
#
# The greedy plan above is the cheapest possible, but when prices fall slowly
# along a highway it "buys just enough to reach the next cheaper station" over
# and over: 20+ stops, some for half a gallon to save a cent. Real drivers
# pay for every stop in time and detour, so the practical objective is
#
#     minimise   fuel cost + stop_penalty * number_of_stops
#
# For a fixed set of stops, an optimal plan only ever does one of two things
# at each stop v before the next stop w (Khuller, Malekian & Mestre, "To fill
# or not to fill: the gas station problem"):
#   * w is not more expensive  -> buy just enough to reach w (arrive empty)
#   * w is more expensive      -> fill the tank
# So the fuel you can arrive with at v is either 0, or "full tank minus the
# drive from some earlier stop u". That small state space makes an exact
# dynamic program over (stop, arrival state) fast enough to run per request
# (vectorised with NumPy; ~O(n * w^2) for n stations and w stations per range).


def _dedupe_same_position(miles: Sequence[float], prices: Sequence[float]) -> list[int]:
    """Indices of stations worth considering: at any one position only the
    cheapest station matters (stations geocoded to the same town share coordinates)."""
    keep: list[int] = []
    for i, (mile, price) in enumerate(zip(miles, prices)):
        if keep and abs(miles[keep[-1]] - mile) < EPS_MILES:
            if price < prices[keep[-1]]:
                keep[-1] = i
        else:
            keep.append(i)
    return keep


def plan_fuel_stops_practical(
    miles: Sequence[float],
    prices: Sequence[float],
    total_miles: float,
    vehicle: Vehicle = Vehicle(),
    start_fuel: float | None = None,
    stop_penalty: float = 0.0,
) -> FuelPlan:
    """Minimise fuel cost + `stop_penalty` per stop. With a penalty of 0 this
    is exactly `plan_fuel_stops` (the pure cheapest plan)."""
    if stop_penalty <= 0:
        return plan_fuel_stops(miles, prices, total_miles, vehicle, start_fuel)

    # Validates inputs and feasibility (raises UnreachableSegmentError).
    plan_fuel_stops(miles, prices, total_miles, vehicle, start_fuel)

    import numpy as np

    tank = vehicle.tank_gallons
    f0 = tank if start_fuel is None else min(max(start_fuel, 0.0), tank)
    keep = _dedupe_same_position(miles, prices)
    m = np.array([miles[i] for i in keep], dtype=float)
    c = np.array([prices[i] for i in keep], dtype=float)
    n = len(keep)
    if total_miles <= f0 * vehicle.mpg + EPS_MILES:
        return FuelPlan([], total_miles, f0, f0 - total_miles / vehicle.mpg, vehicle)

    rng = vehicle.range_miles + EPS_MILES
    gal = 1.0 / vehicle.mpg
    # Window of stations reachable on a full tank: (v, hi[v]) ahead, [lo[v], v) behind.
    hi = np.searchsorted(m, m + rng, side="right")
    lo = np.searchsorted(m, m - rng, side="left")
    reaches_dest = (total_miles - m) <= rng

    INF = float("inf")
    # Arrival states at v: 0 = empty, 1 = straight from the origin, 2+k = after
    # filling up at station lo[v] + k.
    best: list[np.ndarray] = [None] * n  # type: ignore[list-item]
    choice: list[np.ndarray] = [None] * n  # type: ignore[list-item]  # encoded next move per state

    for v in range(n - 1, -1, -1):
        preds = np.arange(lo[v], v)
        g = np.concatenate((
            [0.0, f0 - m[v] * gal],
            tank - (m[v] - m[preds]) * gal,
        ))
        valid_state = g >= -EPS_GALLONS
        if m[v] * gal > f0 + EPS_GALLONS:
            valid_state[1] = False
        g = np.maximum(g, 0.0)

        # Option A: last stop, buy enough to reach the destination.
        cost = np.full(g.size, INF)
        move = np.full(g.size, -1, dtype=np.int64)
        if reaches_dest[v]:
            need = (total_miles - m[v]) * gal
            cost = stop_penalty + np.maximum(need - g, 0.0) * c[v]
            move[:] = n  # n encodes "destination"

        ahead = np.arange(v + 1, hi[v])
        if ahead.size:
            need = (m[ahead] - m[v]) * gal  # gallons to reach each w
            cheaper = c[ahead] <= c[v]
            # Option B: next stop w is not pricier -> buy just enough, arrive empty.
            empty_next = np.array([best[w][0] for w in ahead])
            cost_b = stop_penalty + (need[None, :] - g[:, None]) * c[v] + empty_next[None, :]
            cost_b[:, ~cheaper] = INF
            cost_b[g[:, None] > need[None, :] + EPS_GALLONS] = INF
            # Option C: next stop w is pricier -> fill up, arrive with tank - need.
            fill_next = np.array([best[w][2 + v - lo[w]] for w in ahead])
            cost_c = stop_penalty + (tank - g)[:, None] * c[v] + fill_next[None, :]
            cost_c[:, cheaper] = INF

            for options, offset in ((cost_b, 0), (cost_c, n + 1)):
                k = np.argmin(options, axis=1)
                value = options[np.arange(g.size), k]
                better = value < cost
                cost = np.where(better, value, cost)
                move = np.where(better, offset + ahead[k], move)

        cost[~valid_state] = INF
        best[v], choice[v] = cost, move

    # From the origin: either drive straight to the destination, or to a first stop.
    first_options = [(best[w][1], w) for w in range(n) if m[w] <= f0 * vehicle.mpg + EPS_MILES]
    if not first_options or min(first_options)[0] == INF:
        raise UnreachableSegmentError(0.0, total_miles, vehicle.range_miles)
    _, v = min(first_options)

    purchases: list[Purchase] = []
    state = 1
    fuel = f0 - m[v] * gal
    while True:
        move = int(choice[v][state])
        if move == n:  # destination
            buy = max((total_miles - m[v]) * gal - fuel, 0.0)
            next_v, next_state, arrive = None, None, None
        elif move > n:  # fill up, next stop pricier
            w = move - (n + 1)
            buy = tank - fuel
            next_v, next_state, arrive = w, 2 + v - lo[w], tank - (m[w] - m[v]) * gal
        else:  # just enough to reach a cheaper-or-equal stop
            w = move
            buy = (m[w] - m[v]) * gal - fuel
            next_v, next_state, arrive = w, 0, 0.0
        buy = max(buy, 0.0)
        if buy > EPS_GALLONS:
            purchases.append(Purchase(keep[v], float(m[v]), float(c[v]), float(buy), float(max(fuel, 0.0))))
        if next_v is None:
            end_fuel = fuel + buy - (total_miles - m[v]) * gal
            break
        v, state, fuel = next_v, next_state, arrive

    return FuelPlan(purchases, total_miles, f0, max(end_fuel, 0.0), vehicle)
