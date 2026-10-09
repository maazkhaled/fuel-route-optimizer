# Fuel Route Optimizer

A Django REST API that takes a start and finish anywhere in the USA and returns:

- the driving route (GeoJSON, plus an interactive map page),
- where to fuel up and how many gallons to buy at each stop, chosen for low cost,
- the total spent on fuel.

The vehicle has a 500-mile range, gets 10 mpg (a 50-gallon tank) and starts with a full tank. Prices come from the provided OPIS truck-stop file.

**One external API call per trip.** The only network call is to the free [OSRM](https://project-osrm.org/) routing server. Locations like `"Dallas, TX"` and all ~6,600 stations are resolved locally, and routes are cached, so repeat requests make zero calls.

---

## Quick start

Requires Python 3.12+ (Django 6.1).

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate        # creates SQLite DB and loads the 6,626 geocoded stations
python manage.py runserver
```

- API: `POST http://127.0.0.1:8000/api/route/`
- Map: <http://127.0.0.1:8000/> (opens the map page with example trips)
- Postman: import `postman/FuelRouteOptimizer.postman_collection.json`
- Tests: `python manage.py test planner` (39 tests, no network needed)

Docker alternative: `docker build -t fuel-route . && docker run -p 8000:8000 fuel-route`

No API keys are needed. Settings are environment variables with working defaults (see `.env.example`).

---

## API

### `POST /api/route/`

```json
{ "start": "Chicago, IL", "finish": "Dallas, TX" }
```

| Field | Required | Description |
|---|---|---|
| `start`, `finish` | yes | `"City, ST"` / `"City, State"`, a street address, `"lat,lon"`, or `{"lat": 41.88, "lon": -87.63}`. Must be in the USA. |
| `geometry` | no | `simplified` (default), `full`, or `none`. Controls the route line returned. |
| `include_stations` | no | `true` also lists every station found along the route. |
| `stop_penalty` | no | USD value of one extra stop (default `5`). `0` = cheapest fuel no matter how many stops. |

The same parameters work as a query string on `GET /api/route/?start=...&finish=...`.

<details>
<summary><b>Example response</b> (real OSRM route, <code>geometry: "none"</code>)</summary>

```json
{
  "start":  { "query": "Chicago, IL", "label": "Chicago, IL", "lat": 41.85003, "lon": -87.65005, "resolved_by": "place_table" },
  "finish": { "query": "Dallas, TX",  "label": "Dallas, TX",  "lat": 32.78306, "lon": -96.80667, "resolved_by": "place_table" },
  "route": { "provider": "osrm", "distance_miles": 966.9, "duration_hours": 17.1 },
  "fuel_stops": [
    {
      "stop_number": 1,
      "station": { "opis_id": 69861, "name": "HUCKS FOOD & FUEL #379", "address": "I-57, EXIT 53",
                   "city": "Marion", "state": "IL", "lat": 37.73061, "lon": -88.93313 },
      "mile_marker": 316.1, "miles_since_previous_fuel": 316.1, "off_route_miles": 1.4,
      "price_per_gallon": 2.929, "fuel_on_arrival_gallons": 18.39, "gallons": 30.37, "cost": 88.95
    },
    {
      "stop_number": 2,
      "station": { "opis_id": 68256, "name": "EXTRA MILE TRUCK STOP", "address": "I-30 EXIT 208",
                   "city": "Hooks", "state": "TX", "lat": 33.46623, "lon": -94.28853 },
      "mile_marker": 803.7, "miles_since_previous_fuel": 487.6, "off_route_miles": 0.3,
      "price_per_gallon": 2.817, "fuel_on_arrival_gallons": 0.0, "gallons": 16.32, "cost": 45.98
    }
  ],
  "summary": {
    "total_fuel_cost": 134.93, "currency": "USD", "fuel_stop_count": 2,
    "gallons_purchased": 46.69, "gallons_used": 96.69,
    "starting_fuel_gallons": 50.0, "fuel_at_finish_gallons": 0.0,
    "average_price_paid": 2.89, "stations_along_route": 203, "stop_penalty_usd": 5.0,
    "fuel_only_optimum": { "total_fuel_cost": 134.77, "fuel_stop_count": 4,
                           "note": "Cheapest possible fuel bill if extra stops cost nothing." }
  },
  "assumptions": {
    "vehicle_range_miles": 500.0, "miles_per_gallon": 10.0, "tank_gallons": 50.0,
    "starts_with": "full tank", "total_fuel_cost_means": "money spent at fuel stops during this trip",
    "optimises": "fuel cost + stop_penalty_usd per stop (avoids stopping for a few cents)",
    "corridor_miles": 10.0, "station_location_precision": "town-level (source data has no coordinates)"
  },
  "meta": {
    "external_api_calls": { "routing": 1, "geocoding": 0, "total": 1 },
    "timings_ms": { "routing": 416.0, "planning": 8.4, "total": 425.1 }
  },
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=Chicago%2C+IL&finish=Dallas%2C+TX"
}
```
</details>

### `GET /api/route/map/?start=...&finish=...`

An interactive Leaflet map: the route, numbered fuel stops (click for price, gallons, cost), every station in the corridor, and a summary panel. It reuses the cached route, so opening `map_url` right after an API call makes no extra routing request.

### `GET /api/health/`

Returns `{"status": "ok", "stations_loaded": 6626, "places_loaded": 34760}`.

### Errors

All errors share one shape: `{"error": {"code": "...", "message": "...", "details": {...}}}`.

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Missing/malformed fields (`details` lists them) |
| 400 | `outside_usa` | A location is outside the USA |
| 400 | `same_location` | Start and finish are the same place |
| 422 | `location_not_found` | Text could not be matched to a US location |
| 422 | `no_route` | No drivable route (e.g. Honolulu → Dallas) |
| 422 | `no_fuel_within_range` | Some stretch of the route has no station within 500 miles (`details` gives the miles) |
| 502 | `routing_unavailable` | The routing service failed |
| 503 | `geocoder_unavailable` | Free-text geocoding failed (City/ST and lat/lon still work) |

---

## How it works

```
 start, finish ──► resolve locations ──► route (OSRM) ──► stations along route ──► fuel optimizer ──► JSON / map
                   local table, 0 calls   1 call, cached   in-memory KD-tree        dynamic program
```

### 1. Station data: geocoded once, offline

> Full walkthrough with worked examples: **[docs/DATA_INGESTION.md](docs/DATA_INGESTION.md)**.

The price file has **no coordinates**, and its addresses are highway-exit descriptions (`"I-44, EXIT 283 & US-69"`) that geocoders can't resolve. Every row does have a town and state, so `python manage.py prepare_stations` places each station at its town:

| Step | Result |
|---|---|
| Raw rows | 8,151 |
| Canadian rows dropped (AB, BC, ON, …) | −620 |
| Duplicate listings of the same station ID merged | −905 (487 stations had different prices across listings; the lowest is kept, configurable with `--duplicate-price`) |
| Stations | **6,626** |
| Geocoded via GeoNames places | 5,703 |
| Geocoded via US-Cities-Database (small towns) | 920 |
| Geocoded via a manual alias (e.g. Henrico → Glen Allen, VA) | 3 |
| **Coverage** | **100%** |

The output (`planner/data/stations_geocoded.csv`) is committed and loaded by `migrate`, so nothing is geocoded at request time. The same 34,760-entry place table resolves user input like `"St. Louis, MO"` locally. Only unrecognised free text (a street address) falls back to Nominatim, which is cached.

### 2. Route: one call

The app requests the route from OSRM with `overview=full&geometries=polyline6`. Polyline6 is about 5× smaller than GeoJSON: a New York → Los Angeles route is ~160 KB instead of ~0.8 MB. That trims transfer time, and the full resolution keeps matching accurate. If `ORS_API_KEY` is set, OpenRouteService is used as a fallback when OSRM fails.

### 3. Stations along the route: local, in milliseconds

All stations are held in memory as NumPy arrays (loaded once per process). For each request:

1. The route is resampled so points are ≤ 0.5 mi apart. OSRM only emits vertices at bends, and straight interstates can have miles between them.
2. Stations are pre-filtered to the route's bounding box.
3. A KD-tree over the route points (as 3D unit vectors, so distances are spherical) finds each station's nearest route point.
4. A station is kept if it's within the **10-mile corridor**. Its **mile marker** is that route point's distance from the start.

The corridor is 10 miles because stations are located at their town's centre, which can be a few miles from the interstate exit. On the real Chicago → Dallas route the chosen stops were 0.3 and 1.4 miles off the line, at I-57 and I-30 exits, and the plan is identical at corridor widths of 3–15 miles.

### 4. The optimizer

Every trip burns a fixed amount of fuel (distance ÷ 10 mpg), so the only decision is *where* to buy those gallons and *how many* at each stop.

**Cheapest plan (greedy, O(n)).** Treat the destination as a $0 station. At each station:
- If a cheaper station is within 500 miles, buy only enough to reach the **first** cheaper one.
- Otherwise this is the cheapest fuel reachable on a full tank: fill up.

"First cheaper station ahead" for every station comes from one monotonic-stack pass. A test checks this against an exact **linear-programming** solver on 600 random trips.

**Practical plan (default).** The pure cheapest plan can be silly when prices fall gradually. On Seattle → Miami it makes 22 stops, some buying half a gallon to save a cent. So the API minimises:

```
fuel cost + stop_penalty × number_of_stops        (stop_penalty defaults to $5)
```

This is an exact dynamic program over (station, fuel on arrival). It relies on a known property of the problem (Khuller, Malekian & Mestre, *To fill or not to fill: the gas station problem*): for consecutive stops, an optimal plan either fills the tank or buys just enough to reach the next stop. So the fuel you can arrive with is either 0, or a full tank minus the drive from some earlier stop. That keeps the state space small, and the DP is vectorised with NumPy. A test checks it against a brute-force search over every possible set of stops.

| Synthetic interstate trip | Cheapest plan | Practical plan ($5/stop) |
|---|---|---|
| Seattle → Miami | 22 stops, $775.09 | **7 stops, $778.36** |
| New York → Los Angeles | 10 stops, $601.43 | **6 stops, $604.43** |
| Chicago → Dallas (real OSRM route) | 4 stops, $134.77 | **2 stops, $134.93** |

The response reports both (`fuel_only_optimum`), so the trade-off is visible. Send `"stop_penalty": 0` for the pure cheapest plan.

---

## Assumptions

- The vehicle **starts with a full tank** (50 gal). `total_fuel_cost` is money spent at fuel stops during the trip, so a trip under 500 miles needs no stops and costs $0. `gallons_used` is always reported.
- Range is exactly 500 miles with no safety reserve, so the plan may arrive at a stop nearly empty.
- Fuel is bought only at stations in the file. A station listed multiple times uses its lowest price.
- Distances to stations are measured along the route. The short detour off the highway to the station isn't charged.
- Station positions are town-level (the data has no coordinates). `off_route_miles` is reported per stop.

## Performance

| Step | Time |
|---|---|
| Resolve `"City, ST"` | < 1 ms (local) |
| OSRM route, cross-country | ~0.4 s (the only network call) |
| Densify + match + optimise | ~5–15 ms |
| Repeat request (cached route) | ~10–20 ms end to end |

The station and place tables are loaded into memory when the WSGI app starts, so the first request doesn't pay for it.

## Tests

`python manage.py test planner`. 39 tests, all offline (the routing API is mocked):

- **Optimizer:** worked examples, the full-tank short trip, unreachable gaps, **LP optimality on 600 random trips**, and **brute-force optimality** of the stop-penalty DP.
- **Geometry:** distances, resampling, polyline decoding (Google reference vector), simplification, US bounds.
- **Locations:** City/ST variants (`St.`, `Ft`, ZIP, `", USA"`), coordinates, geocoder fallback and caching, errors.
- **API end to end:** a **real OSRM Chicago → Dallas geometry** fixture, cross-country plans (range and tank invariants, cost totals), caching (0 calls on repeat), every error code, and the map page.

## Project layout

```
config/                     settings, root URLs
planner/
  models.py                 FuelStation
  views.py / urls.py        API, map page, health
  serializers.py            request validation
  exceptions.py             service errors -> consistent HTTP errors
  services/
    trip.py                 orchestrates one plan
    optimizer.py            greedy cheapest plan + stop-penalty DP
    stations.py             in-memory station index, corridor matching
    routing.py              OSRM client (+ ORS fallback), caching
    locations.py            input -> coordinates (local table first)
    places.py               US place table, name normalisation
    geo.py                  haversine, resampling, polyline, simplification
  management/commands/
    prepare_stations.py     raw CSV -> geocoded CSV (offline)
    load_stations.py        reload the table from CSV
  migrations/0002_*.py      loads stations on `migrate`
  data/                     raw file, place sources, generated tables
  templates/planner/map.html
  tests/
postman/                    Postman collection
```

## What I'd do next

- **Exact station coordinates.** Match stations to OpenStreetMap fuel POIs by name and exit to replace town-level positions.
- **PostgreSQL + PostGIS.** `ST_DWithin` and `ST_LineLocatePoint` would replace the in-memory index if the station set grew beyond memory.
- **Self-hosted OSRM with a truck profile** (and no public rate limits), plus a shared Redis cache across workers.
- **Detour-aware costs and a fuel reserve.** Charge the miles to leave the highway, and keep a configurable safety margin.

## Data sources

- Fuel prices: provided assessment file (`planner/data/raw/`).
- Place coordinates: [GeoNames](https://www.geonames.org/) (CC BY 4.0, via the `cities.json` package) and [kelvins/US-Cities-Database](https://github.com/kelvins/US-Cities-Database) (MIT).
- Routing: [OSRM](https://project-osrm.org/) public demo server. Map tiles: Esri World Street Map (Leaflet), data © OpenStreetMap contributors and others.
