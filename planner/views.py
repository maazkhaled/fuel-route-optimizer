from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from django.views import View
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import describe_service_error
from .serializers import RoutePlanRequestSerializer
from .services.locations import LocationError
from .services.places import PlaceIndex
from .services.stations import StationIndex
from .services.trip import plan_trip


def _as_query_value(location) -> str:
    if isinstance(location, dict):
        lat = location.get("lat", location.get("latitude"))
        lon = location.get("lon", location.get("lng", location.get("longitude")))
        return f"{lat},{lon}"
    return str(location)


class RoutePlanView(APIView):
    """
    Plan the cheapest fuel stops between two US locations.

    POST /api/route/
        {"start": "New York, NY", "finish": "Los Angeles, CA"}

    GET /api/route/?start=New York, NY&finish=Los Angeles, CA

    Locations may be "City, ST", a free-text address, "lat,lon", or
    {"lat": .., "lon": ..}. Optional: geometry (simplified|full|none),
    include_stations (bool), stop_penalty (USD per stop, default 5).
    """

    def post(self, request):
        return self._plan(request, request.data)

    def get(self, request):
        return self._plan(request, request.query_params.dict())

    def _plan(self, request, payload):
        serializer = RoutePlanRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = plan_trip(
            data["start"],
            data["finish"],
            geometry=data["geometry"],
            include_candidates=data["include_stations"],
            stop_penalty=data.get("stop_penalty"),
        )
        query = {"start": _as_query_value(data["start"]), "finish": _as_query_value(data["finish"])}
        if data.get("stop_penalty") is not None:
            query["stop_penalty"] = data["stop_penalty"]
        result["map_url"] = request.build_absolute_uri(f"{reverse('route-map')}?{urlencode(query)}")
        return Response(result)


class RouteMapView(View):
    """Interactive Leaflet map of a planned route. Reuses the cached route, so
    opening the map right after an API call makes no extra routing request."""

    template_name = "planner/map.html"

    def get(self, request):
        start = request.GET.get("start", "").strip()
        finish = request.GET.get("finish", "").strip()
        context = {"start": start, "finish": finish, "plan": None, "error": None}
        if start and finish:
            try:
                stop_penalty = request.GET.get("stop_penalty")
                context["plan"] = plan_trip(
                    start, finish, geometry="simplified", include_candidates=True,
                    stop_penalty=float(stop_penalty) if stop_penalty not in (None, "") else None,
                )
            except ValueError:
                context["error"] = "stop_penalty must be a number."
            except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed silently
                described = describe_service_error(exc)
                if described is None and not isinstance(exc, LocationError):
                    raise
                context["error"] = described[2] if described else str(exc)
        status = 200 if not context["error"] else 422
        return render(request, self.template_name, context, status=status)


class HealthView(APIView):
    def get(self, request):
        return Response({
            "status": "ok",
            "stations_loaded": len(StationIndex.get()),
            "places_loaded": len(PlaceIndex.get()),
        })
