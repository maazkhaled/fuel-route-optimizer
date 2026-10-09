from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from planner.models import FuelStation
from planner.services.station_data import DEFAULT_STATIONS_CSV, read_station_rows, replace_stations


class Command(BaseCommand):
    help = "Reload the FuelStation table from the geocoded station CSV."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=str(DEFAULT_STATIONS_CSV), help="Geocoded station CSV.")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"{path} not found. Run `python manage.py prepare_stations` first.")
        with transaction.atomic():
            count = replace_stations(FuelStation, read_station_rows(path))
        self.stdout.write(self.style.SUCCESS(f"Loaded {count:,} fuel stations."))
