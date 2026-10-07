from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ErrorResponseSerializer, RoutePlanResponseSerializer, RouteRequestSerializer
from .services.route_service import COST_ASSUMPTION, plan_route

# A real New York to Chicago response, with the route line cut down to its two ends.
EXAMPLE_RESPONSE = {
    "start": {
        "input": "New York, NY",
        "latitude": 40.7127281,
        "longitude": -74.0060152,
        "display_name": "New York, United States",
    },
    "finish": {
        "input": "Chicago, IL",
        "latitude": 41.8755616,
        "longitude": -87.6244212,
        "display_name": "Chicago, South Chicago Township, Cook County, Illinois, United States",
    },
    "route": {
        "distance_miles": 790.57,
        "duration_minutes": 890.8,
        "geometry": {"type": "LineString", "coordinates": [[-74.005737, 40.712118], [-87.624351, 41.875563]]},
    },
    "vehicle": {"max_range_miles": 500, "fuel_efficiency_mpg": 10.0, "tank_capacity_gallons": 50.0},
    "fuel": {
        "total_fuel_required_gallons": 79.056,
        "initial_fuel_gallons": 50.0,
        "fuel_purchased_gallons": 29.056,
        "fuel_at_destination_gallons": 0.0,
        "total_cost": 88.88,
        "currency": "USD",
        "cost_assumption": COST_ASSUMPTION,
    },
    "fuel_stops": [
        {
            "sequence": 1,
            "station": {
                "id": 6237,
                "opis_id": 72445,
                "name": "SHEETZ #639",
                "address": "I-80 Exit 223",
                "city": "Youngstown",
                "state": "OH",
                "latitude": 41.1035786,
                "longitude": -80.6520161,
                "price_per_gallon": 3.059,
            },
            "route_distance_miles": 391.008,
            "distance_from_previous_stop_miles": 391.008,
            "distance_from_route_miles": 3.37,
            "fuel_before_stop_gallons": 10.899,
            "fuel_purchased_gallons": 29.056,
            "fuel_cost": 88.88,
        }
    ],
    "map": {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-74.0060152, 40.7127281]},
                "properties": {"marker": "start", "label": "New York, NY"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-87.6244212, 41.8755616]},
                "properties": {"marker": "finish", "label": "Chicago, IL"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-80.6520161, 41.1035786]},
                "properties": {
                    "marker": "fuel_station",
                    "id": 6237,
                    "name": "SHEETZ #639",
                    "price_per_gallon": 3.059,
                    "route_distance_miles": 391.0,
                    "selected": True,
                    "sequence": 1,
                },
            },
        ],
    },
    "meta": {
        "corridor_miles": 10.0,
        "stop_penalty_dollars": 5.0,
        "candidate_station_count": 224,
        "route_cached": False,
    },
}


def _error(description: str) -> OpenApiResponse:
    return OpenApiResponse(ErrorResponseSerializer, description=description)


class OptimizeRouteView(APIView):
    @extend_schema(
        summary="Plan a trip and where to buy fuel",
        description=(
            "Give it a start and finish in the USA, as place names, addresses or coordinates. It returns the "
            "driving route, the fuel stops to make and what the fuel will cost. The vehicle has a 500-mile "
            "range, does 10 MPG and starts with a full tank. Stops are chosen to keep the fuel bill low "
            "without stopping more often than it's worth."
        ),
        request=RouteRequestSerializer,
        responses={
            200: RoutePlanResponseSerializer,
            400: _error("INVALID_REQUEST, SAME_LOCATION or LOCATION_OUTSIDE_USA"),
            404: _error("LOCATION_NOT_FOUND"),
            422: _error("ROUTE_NOT_FOUND or NO_FEASIBLE_FUEL_PLAN"),
            429: _error("THROTTLED"),
            502: _error("GEOCODING_SERVICE_ERROR or ROUTING_SERVICE_ERROR"),
        },
        examples=[
            OpenApiExample("Place names", value={"start": "New York, NY", "finish": "Chicago, IL"}, request_only=True),
            OpenApiExample(
                "Coordinates",
                value={"start": {"lat": 40.7128, "lng": -74.006}, "finish": {"lat": 41.8781, "lng": -87.6298}},
                request_only=True,
            ),
            OpenApiExample("Successful plan", value=EXAMPLE_RESPONSE, response_only=True, status_codes=["200"]),
            OpenApiExample(
                "Unknown location",
                value={
                    "error": {
                        "code": "LOCATION_NOT_FOUND",
                        "message": "Could not resolve the start location to a place in the USA.",
                    }
                },
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(plan_route(data["start"], data["finish"], data["include_all_stations"]))
