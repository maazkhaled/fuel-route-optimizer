"""Load the committed, pre-geocoded station table so `migrate` is the only setup step."""

from django.db import migrations

from planner.services.station_data import read_station_rows, replace_stations


def load(apps, schema_editor):
    replace_stations(apps.get_model("planner", "FuelStation"), read_station_rows())


def unload(apps, schema_editor):
    apps.get_model("planner", "FuelStation").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("planner", "0001_initial")]
    operations = [migrations.RunPython(load, unload)]
