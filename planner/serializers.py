from rest_framework import serializers

from .services.trip import GEOMETRY_CHOICES


class LocationField(serializers.Field):
    """A US location: "City, ST", a free-text address, "lat,lon", or {"lat": .., "lon": ..}."""

    default_error_messages = {
        "invalid": 'Use "City, ST", an address, "lat,lon", or {{"lat": 41.88, "lon": -87.63}}.',
    }

    def to_internal_value(self, data):
        if isinstance(data, dict):
            if not ({"lat", "latitude"} & data.keys() and {"lon", "lng", "longitude"} & data.keys()):
                self.fail("invalid")
            return data
        if isinstance(data, str) and data.strip():
            if len(data) > 200:
                self.fail("invalid")
            return data.strip()
        self.fail("invalid")

    def to_representation(self, value):
        return value


class RoutePlanRequestSerializer(serializers.Serializer):
    start = LocationField(help_text='e.g. "New York, NY" or {"lat": 40.71, "lon": -74.0}')
    finish = LocationField(help_text='e.g. "Los Angeles, CA"')
    geometry = serializers.ChoiceField(
        choices=GEOMETRY_CHOICES, default="simplified",
        help_text="Route line detail in the response: simplified (default), full, or none.",
    )
    include_stations = serializers.BooleanField(
        default=False, help_text="Also list every station found along the route."
    )
    stop_penalty = serializers.FloatField(
        required=False, allow_null=True, min_value=0, max_value=500,
        help_text="USD cost assigned to each extra stop (default 5). 0 = cheapest fuel regardless of stops.",
    )
