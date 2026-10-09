# How the fuel price data is ingested

**Problem:** the provided CSV says *what each station charges* but not *where it is*.
There are no coordinates, and the addresses are highway-exit text such as
`I-44, EXIT 283 & US-69`, which no geocoder can resolve. To find stations along a route,
every station needs a latitude and longitude.

**Solution:** every row *does* have a town and a state. So I join each station's
`city + state` to a table of US towns that has coordinates. This runs **once, offline**,
and the result is committed to the repo, so the API never geocodes stations at request time.

```
 RAW CSV (8,151 rows)                      PLACE TABLE (34,760 US towns)
 name, address, CITY, STATE, price         CITY|STATE -> latitude, longitude
            │                                         ▲
            │  1. clean: drop Canada, merge duplicates │ built from GeoNames + US-Cities-Database
            ▼                                         │   + 3 hand-written aliases
     6,626 unique US stations                         │
            │  2. normalize the name  ("St. Louis" -> "SAINT LOUIS|MO")
            └──────────────── 3. JOIN on CITY|STATE ──┘
                                   │
                                   ▼
              stations_geocoded.csv  (6,626 rows, each now has lat/lon)
                                   │  4. `migrate` loads it into SQLite
                                   ▼
              FuelStation table  ->  held in memory at runtime (KD-tree matching)
```

Command that does steps 1–3: `python manage.py prepare_stations`
(code: `planner/management/commands/prepare_stations.py`). Re-running it from the raw file
reproduces the committed output exactly.

## Step 1: Clean the raw file

| What happens | Rows |
|---|---|
| Raw rows in the file | 8,151 |
| Canadian rows dropped (AB, BC, ON …), because this is a US-only API | −620 |
| Duplicate listings merged (the same `OPIS Truckstop ID` appears more than once) | −905 |
| **Unique US stations** | **6,626** |

*Example, a duplicate:* station `20` appears twice, as "PILOT TRAVEL CENTER #1243" and
"PILOT #1243", both in Gila Bend, AZ. It becomes **one** station, `listing_count = 2`.
When a station's listings have different prices, the **lowest** is kept. That is
configurable with `--duplicate-price min|mean|max`. 487 stations had differing prices.

*Example, a dropped row:* `FLYING J #850, TCH-16, Edmonton, AB` is Canadian and is removed.

## Step 2: Build the place table (the "where is this town" lookup)

`planner/data/us_places.csv` has one row per `CITY|STATE`, for example:

```
key,name,state,latitude,longitude,source
BIG CABIN|OK,Big Cabin,OK,36.60793,-95.27474,us_cities
```

I did not invent these coordinates. The table is merged from two public datasets plus
a few manual aliases (all in `planner/data/sources/`):

| Source | License | Role |
|---|---|---|
| **GeoNames** US places, via the `cities.json` npm package | CC BY 4.0 | Primary: ~17k towns |
| **kelvins/US-Cities-Database** (GitHub) | MIT | Fills small towns GeoNames lacks: ~30k places |
| `place_aliases.csv` (hand-written) | n/a | 3–4 names neither dataset has |

Merge rules (`build_places()`):
- Prefer GeoNames when it has the town.
- If a name appears twice in one state (two "Albany, MO"), use the second dataset's location to pick the right one.
- If GeoNames lacks the town, use US-Cities-Database. When it lists a city once per county, prefer the county named after the city (Dallas in Dallas County).
- Apply aliases, for example `Henrico, VA` → `Glen Allen, VA` (Henrico is a county, and Glen Allen is where those exits are) and `Elizabethport, NJ` → `Elizabeth, NJ`.

Result: **34,760 places**.

## Step 3: Match each station to a place

The match key is the **normalized** `CITY|STATE`. Normalizing makes spelling variants line up
(`normalize_place` in `planner/services/places.py`): uppercase, strip punctuation, and expand
abbreviations (`St.` → `SAINT`, `Ft` → `FORT`, `Mt` → `MOUNT`). A space-insensitive
fallback handles `BROOKPARK` vs `BROOK PARK`.

*Worked example, station 7:*

```
raw row:     7, "WOODSHED OF BIG CABIN", "I-44, EXIT 283 & US-69", Big Cabin, OK, 3.0073
normalize:   "Big Cabin", "OK"  ->  key  BIG CABIN|OK
look up:     BIG CABIN|OK  ->  36.60793, -95.27474   (source: us_cities)
result row:  7, WOODSHED OF BIG CABIN, ..., price 3.007333, lat 36.60793, lon -95.27474
```

Outcome: **6,626 of 6,626 stations (100%) matched**: 5,703 via GeoNames, 920 via
US-Cities-Database, 3 via aliases. If a town ever fails to match, the command lists it,
and `--nominatim` can geocode the leftovers at 1 request/second (not needed here).

## Step 4: Load into the app

`stations_geocoded.csv` is committed. Migration `0002_load_stations` reads it into the
`FuelStation` table, so **`python manage.py migrate` is the only setup step**.
At server start the stations are read into NumPy arrays (`StationIndex`), and requests
never query the database for stations.

## The same table also resolves user input

Typing `"Chicago, IL"` runs the identical `CITY|STATE` lookup against `us_places.csv`, so
common inputs need **no** geocoding call. Only free-text street addresses go to Nominatim
(OpenStreetMap's free geocoder, rate-limited, cached).

## Known limitation (by design)

A station is placed at **the centre of its town**, not its exact pump. A truck stop on the
interstate can be a few miles from the town centre. To absorb that, the route-matching step
accepts stations within a **10-mile corridor** of the route. On the Chicago → Dallas test
route, the chosen stops landed 0.3–1.4 miles from the road. With exact pump coordinates
(a paid geocoding pass), the corridor could be much tighter.
