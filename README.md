# Fuel Route Optimizer

A Django API that takes a start and a finish in the USA and tells you where to stop for fuel and how much it will cost. It returns the route, the fuel stops (with gallons to buy at each), and the total spent on fuel. There's also a small map page that draws it all.

The vehicle can go 500 miles on a tank, does 10 mpg (so a 50 gallon tank), and starts full. Prices come from the fuel price file that came with the assignment.

I wanted to keep the calls to the routing API down, so each trip makes one call to OSRM (a free routing server) and nothing else. City names and the ~6,600 stations are all looked up locally, and routes are cached, so asking for the same trip twice makes no external calls at all.

## Running it

You need Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate         # creates the SQLite DB and loads the stations
python manage.py runserver
```

Then:

- API: `POST http://127.0.0.1:8000/api/route/`
- Map page: http://127.0.0.1:8000/
- Postman: import `postman/FuelRouteOptimizer.postman_collection.json`
- Tests: `python manage.py test planner` (39 tests, none need the network)

There's also a Dockerfile if you'd rather use that. No API keys are needed.

## Using the API

```json
POST /api/route/
{ "start": "Chicago, IL", "finish": "Dallas, TX" }
```

| Field | Required | What it does |
|---|---|---|
| `start`, `finish` | yes | `"City, ST"`, a street address, `"lat,lon"`, or `{"lat": 41.88, "lon": -87.63}`. Has to be in the USA. |
| `geometry` | no | `simplified` (default), `full` or `none`. How much of the route line to send back. |
| `include_stations` | no | `true` also lists every station found along the route. |
| `stop_penalty` | no | What one extra stop is worth in dollars (default 5). Set it to 0 to get the cheapest fuel regardless of how many stops it takes. |

The same options work as query params on `GET /api/route/`.

Here is a trimmed response for Chicago to Dallas:

```json
{
  "start":  { "label": "Chicago, IL", "lat": 41.85003, "lon": -87.65005, "resolved_by": "place_table" },
  "finish": { "label": "Dallas, TX",  "lat": 32.78306, "lon": -96.80667, "resolved_by": "place_table" },
  "route": { "provider": "osrm", "distance_miles": 966.9, "duration_hours": 17.1 },
  "fuel_stops": [
    {
      "stop_number": 1,
      "station": { "name": "HUCKS FOOD & FUEL #379", "address": "I-57, EXIT 53", "city": "Marion", "state": "IL" },
      "mile_marker": 316.1, "off_route_miles": 1.4,
      "price_per_gallon": 2.929, "gallons": 30.37, "cost": 88.95
    },
    {
      "stop_number": 2,
      "station": { "name": "EXTRA MILE TRUCK STOP", "address": "I-30 EXIT 208", "city": "Hooks", "state": "TX" },
      "mile_marker": 803.7, "off_route_miles": 0.3,
      "price_per_gallon": 2.817, "gallons": 16.32, "cost": 45.98
    }
  ],
  "summary": { "total_fuel_cost": 134.93, "fuel_stop_count": 2, "gallons_used": 96.69 },
  "meta": { "external_api_calls": { "routing": 1, "geocoding": 0, "total": 1 } }
}
```

The real response has more in it (the route line, assumptions, timings), but that's the shape.

`GET /api/route/map/?start=...&finish=...` gives you the Leaflet map with the route, numbered stops and a summary panel. `GET /api/health/` just confirms the app is up and how many stations loaded.

Errors all look like `{"error": {"code": "...", "message": "..."}}`. The codes are `invalid_request`, `outside_usa`, `same_location`, `location_not_found`, `no_route`, `no_fuel_within_range`, `routing_unavailable` and `geocoder_unavailable`.

## How it works

1. Turn the start and finish into coordinates.
2. Ask OSRM for the driving route (one call, cached).
3. Find the stations near that route.
4. Work out where to buy fuel and how much.

### The data

There are really two datasets involved, and it helps to keep them apart.

The first is the fuel price file from the assignment. It has a station name, address, city, state and a price, but no coordinates, and the addresses are things like `I-44, EXIT 283 & US-69` that a geocoder can't do anything with. So on its own I can't tell where a station is.

The second is a table of US towns and their coordinates, which I put together from two open datasets: [GeoNames](https://www.geonames.org/) (via the `cities.json` package) and [kelvins/US-Cities-Database](https://github.com/kelvins/US-Cities-Database). It has about 34,700 places and no prices.

Every row in the price file does have a city and state, so I join the two on that. `python manage.py prepare_stations` does it, once, ahead of time:

- the raw file has 8,151 rows
- 620 of those are Canadian, so they're dropped
- 905 are duplicate listings of the same station ID, merged into one (the lowest price wins, since a few stations had different prices across listings)
- that leaves 6,626 stations, and every one of them found a match (a handful needed a manual alias, like Henrico, VA mapping to Glen Allen)

The result is saved as `planner/data/stations_geocoded.csv` and a migration loads it, which is why `migrate` is the only setup step. Nothing gets geocoded while the API is running.

The same town table is also what turns a typed `"Chicago, IL"` into coordinates, so that costs no API call either. Only a free-text street address falls through to Nominatim (OpenStreetMap's geocoder), and that result is cached.

The catch is that stations end up at the centre of their town rather than at the actual pump. I deal with that in the next step.

### Finding stations along the route

OSRM returns the route as a line. For each request I:

1. add points so there's one at least every half mile (OSRM only gives you points at bends, so a straight interstate can go miles without one)
2. throw out stations outside the route's bounding box
3. use a KD-tree to find each remaining station's nearest point on the route
4. keep the ones within 10 miles, and record how far along the route they are (the mile marker)

The corridor is 10 miles wide because of the town-centre problem above. On the real Chicago to Dallas route the stops it picked ended up 0.3 and 1.4 miles from the road, and the plan didn't change with corridors anywhere between 3 and 15 miles.

### Choosing where to fuel up

The trip uses a fixed amount of fuel (distance divided by 10), so the only question is where to buy it.

The basic rule: at each station, if there's a cheaper one within 500 miles ahead, buy just enough to get to the first cheaper one. If there isn't, fill the tank. I treat the destination as a free station so the last leg works out. This gives the cheapest possible fuel bill, and I checked it against a linear programming solver on 600 random trips.

The problem is that it can suggest silly plans. When prices drift down slowly it will stop over and over to save a cent: Seattle to Miami comes out at 22 stops. So the default is a second approach, a dynamic program that minimises fuel cost plus a penalty per stop ($5 by default, and you can change it with `stop_penalty`). It leans on a known result for this problem (Khuller, Malekian and Mestre, *To fill or not to fill: the gas station problem*) that you either fill up or buy just enough to reach the next stop, which keeps the search small. I checked it against brute force.

| Trip | Cheapest fuel only | Default ($5 per stop) |
|---|---|---|
| Seattle to Miami (synthetic route) | 22 stops, $775.09 | 7 stops, $778.36 |
| New York to Los Angeles (synthetic route) | 10 stops, $601.43 | 6 stops, $604.43 |
| Chicago to Dallas (real OSRM route) | 4 stops, $134.77 | 2 stops, $134.93 |

Every response includes the pure cheapest number as `fuel_only_optimum` so you can see what the penalty costs.

## Assumptions

- The vehicle starts with a full tank, and `total_fuel_cost` only counts fuel bought along the way. So a trip under 500 miles needs no stops and costs $0 (`gallons_used` is still reported).
- The range is exactly 500 miles with no safety reserve, so it can roll into a stop nearly empty.
- The small detour from the highway to a station isn't charged for.
- Station positions are town-level, because the data has no coordinates. Each stop reports `off_route_miles`.

## Speed

Looking up a `"City, ST"` takes under a millisecond. The OSRM call is the slow part at about 0.4 seconds for a cross-country route. Matching stations and optimising together take around 5 to 15 ms. A repeat request with a cached route comes back in roughly 10 to 20 ms. The station and town tables are loaded when the app starts, so the first request doesn't pay for that.

## Tests

`python manage.py test planner` runs 39 tests with the routing API mocked. They cover the optimizer (including the LP and brute force checks), the geometry helpers, location parsing, and the API end to end, including a real OSRM Chicago to Dallas route saved as a fixture.

## Layout

```
config/                  settings and root URLs
planner/
  views.py, urls.py      API, map page, health check
  services/
    trip.py              runs one plan start to finish
    optimizer.py         the greedy rule and the stop-penalty DP
    stations.py          in-memory stations and corridor matching
    routing.py           OSRM client (with an optional ORS fallback), caching
    locations.py         turns input into coordinates
    places.py            the town table and name cleanup
    geo.py               distances, resampling, polyline decoding
  management/commands/
    prepare_stations.py  raw CSV to geocoded CSV (run once, offline)
  data/                  raw file, place sources, generated tables
  templates/planner/     the map page
  tests/
postman/                 Postman collection
```

## If I had more time

- Get exact station coordinates, probably by matching stations to OpenStreetMap fuel locations by name and exit number.
- Move to PostgreSQL with PostGIS if the station list got too big for memory.
- Run my own OSRM with a truck profile and share the cache across workers with Redis.
- Charge for the detour off the highway and keep a fuel reserve.

## Credits

Place coordinates are from GeoNames (CC BY 4.0) and kelvins/US-Cities-Database (MIT). Routing is the public [OSRM](https://project-osrm.org/) demo server. Map tiles are Esri World Street Map, drawn with Leaflet, with data from OpenStreetMap contributors and others.
