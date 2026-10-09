from django.db import models


class FuelStation(models.Model):
    """A truck stop from the provided price file, geocoded to its town."""

    opis_id = models.PositiveIntegerField(unique=True, help_text="OPIS Truckstop ID from the source file.")
    name = models.CharField(max_length=128)
    address = models.CharField(max_length=255, help_text="Highway/exit description, e.g. 'I-44, EXIT 283'.")
    city = models.CharField(max_length=64)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=9, decimal_places=6, help_text="Retail price, USD per gallon.")
    latitude = models.FloatField()
    longitude = models.FloatField()
    geocode_source = models.CharField(
        max_length=32, help_text="Which dataset supplied the coordinates (town-level precision)."
    )
    listing_count = models.PositiveSmallIntegerField(
        default=1, help_text="How many rows the source file had for this station ID."
    )

    class Meta:
        ordering = ["opis_id"]
        indexes = [models.Index(fields=["latitude", "longitude"], name="station_lat_lon_idx")]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.price:.3f}"
