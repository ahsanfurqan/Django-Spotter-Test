from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .services.route_service import LocationInput

MAX_LOCATION_LENGTH = 200


class CoordinateSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)


@extend_schema_field(
    {
        "oneOf": [
            {"type": "string", "maxLength": MAX_LOCATION_LENGTH, "example": "New York, NY"},
            {
                "type": "object",
                "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}},
                "required": ["lat", "lng"],
                "example": {"lat": 40.7128, "lng": -74.006},
            },
        ]
    }
)
class LocationField(serializers.Field):
    """A US place as free text (city, address, ZIP...) or as a {"lat", "lng"} object."""

    def to_internal_value(self, data) -> LocationInput:
        if isinstance(data, str):
            text = " ".join(data.split())
            if not text:
                raise serializers.ValidationError("Location must not be blank.")
            if len(text) > MAX_LOCATION_LENGTH:
                raise serializers.ValidationError(f"Location must be at most {MAX_LOCATION_LENGTH} characters.")
            return LocationInput(query=text)
        if isinstance(data, dict):
            coordinates = CoordinateSerializer(data=data)
            coordinates.is_valid(raise_exception=True)
            return LocationInput(
                latitude=coordinates.validated_data["lat"], longitude=coordinates.validated_data["lng"]
            )
        raise serializers.ValidationError('Expected an address string or an object like {"lat": 40.7, "lng": -74.0}.')

    def to_representation(self, value):
        return value


class RouteRequestSerializer(serializers.Serializer):
    start = LocationField()
    finish = LocationField()
    include_all_stations = serializers.BooleanField(
        default=False,
        help_text="Also include every fuel station near the route on the map, not just the ones to stop at.",
    )

    def validate(self, attrs):
        start, finish = attrs["start"], attrs["finish"]
        if start.query is not None and finish.query is not None and start.query.lower() == finish.query.lower():
            raise serializers.ValidationError({"finish": "Start and finish must be different locations."})
        return attrs


# The serializers below only describe the response for the API docs. The view returns the service's
# dict as-is, because pushing a few thousand route points through serializer fields is slow.
class ResolvedLocationSerializer(serializers.Serializer):
    input = serializers.JSONField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    display_name = serializers.CharField()


class GeometrySerializer(serializers.Serializer):
    type = serializers.CharField(default="LineString")
    coordinates = serializers.ListField(child=serializers.ListField(child=serializers.FloatField()))


class RouteSummarySerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    duration_minutes = serializers.FloatField()
    geometry = GeometrySerializer()


class VehicleSerializer(serializers.Serializer):
    max_range_miles = serializers.IntegerField()
    fuel_efficiency_mpg = serializers.DecimalField(max_digits=6, decimal_places=2)
    tank_capacity_gallons = serializers.DecimalField(max_digits=6, decimal_places=2)


class FuelSummarySerializer(serializers.Serializer):
    total_fuel_required_gallons = serializers.DecimalField(max_digits=10, decimal_places=3)
    initial_fuel_gallons = serializers.DecimalField(max_digits=10, decimal_places=3)
    fuel_purchased_gallons = serializers.DecimalField(max_digits=10, decimal_places=3)
    fuel_at_destination_gallons = serializers.DecimalField(max_digits=10, decimal_places=3)
    total_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()
    cost_assumption = serializers.CharField()


class StationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    price_per_gallon = serializers.DecimalField(max_digits=10, decimal_places=8)


class FuelStopSerializer(serializers.Serializer):
    sequence = serializers.IntegerField()
    station = StationSerializer()
    route_distance_miles = serializers.DecimalField(max_digits=10, decimal_places=3)
    distance_from_previous_stop_miles = serializers.DecimalField(max_digits=10, decimal_places=3)
    distance_from_route_miles = serializers.FloatField()
    fuel_before_stop_gallons = serializers.DecimalField(max_digits=10, decimal_places=3)
    fuel_purchased_gallons = serializers.DecimalField(max_digits=10, decimal_places=3)
    fuel_cost = serializers.DecimalField(max_digits=12, decimal_places=2)


class MapSerializer(serializers.Serializer):
    type = serializers.CharField(default="FeatureCollection")
    features = serializers.ListField(
        child=serializers.DictField(),
        help_text="Map markers as GeoJSON points: start, finish and the fuel stops. With include_all_stations, "
        "every station near the route is added too, and properties.selected says which ones to stop at. "
        "The route line itself is in route.geometry.",
    )


class MetaSerializer(serializers.Serializer):
    corridor_miles = serializers.FloatField()
    stop_penalty_dollars = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        help_text="How much fuel money a stop has to save before the planner will make it.",
    )
    candidate_station_count = serializers.IntegerField()
    route_cached = serializers.BooleanField()


class RoutePlanResponseSerializer(serializers.Serializer):
    start = ResolvedLocationSerializer()
    finish = ResolvedLocationSerializer()
    route = RouteSummarySerializer()
    vehicle = VehicleSerializer()
    fuel = FuelSummarySerializer()
    fuel_stops = FuelStopSerializer(many=True)
    map = MapSerializer()
    meta = MetaSerializer()


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField(required=False)


class ErrorResponseSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()
