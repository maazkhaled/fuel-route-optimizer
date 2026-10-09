# Place data sources

| File | Source | License |
|---|---|---|
| `geonames_us_places.csv` | US subset of the `cities.json` npm package (GeoNames gazetteer data) | Data: GeoNames, CC BY 4.0. Package: see `LICENSE-cities-json.txt` |
| `us_cities_kelvins.csv` | https://github.com/kelvins/US-Cities-Database | MIT, see `LICENSE-us-cities-database.txt` |
| `place_aliases.csv` | Hand-written aliases for towns missing from both tables | — |

`python manage.py prepare_stations` merges these into `../us_places.csv` and geocodes the raw price file into `../stations_geocoded.csv`.
