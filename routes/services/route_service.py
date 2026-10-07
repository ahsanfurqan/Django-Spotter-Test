"""Orchestrates geocoding, routing, station search and fuel optimization for one request."""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from routes.exceptions import (
    LocationNotFoundError,
    LocationOutsideUSAError,
    NoFeasibleFuelPlanError,
    SameLocationError,
)

from .fuel_optimizer import FuelOption, FuelPlan, InfeasibleFuelPlanError, plan_fuel_stops
from .geo import RouteIndex, haversine_miles
from .geocoding import geocode, reverse_geocode
from .routing import Route, get_route
from .station_search import CandidateStation, find_candidate_stations

logger = logging.getLogger(__name__)

SAME_LOCATION_MILES = 0.1
GALLONS = Decimal("0.001")
MILES = Decimal("0.001")
COST_ASSUMPTION = (
    "The vehicle starts with a full tank, which isn't counted in the cost. total_cost is only the fuel "
    "bought at the stops listed, and no more is bought than the trip needs."
)


@dataclass(frozen=True)
class LocationInput:
    query: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class ResolvedLocation:
    input: str | dict
    latitude: float
    longitude: float
    display_name: str

    @property
    def point(self) -> tuple[float, float]:
        return self.latitude, self.longitude


def resolve_location(location: LocationInput, role: str) -> ResolvedLocation:
    if location.query is not None:
        result = geocode(location.query)
        if result is None:
            raise LocationNotFoundError(f"Could not resolve the {role} location to a place in the USA.")
        if result.country_code != "us":
            raise LocationOutsideUSAError(f"The {role} location must be in the USA.")
        return ResolvedLocation(location.query, result.latitude, result.longitude, result.display_name)

    result = reverse_geocode(location.latitude, location.longitude)
    if result is None or result.country_code != "us":
        raise LocationOutsideUSAError(f"The {role} location must be in the USA.")
    return ResolvedLocation(
        {"lat": location.latitude, "lng": location.longitude},
        location.latitude,
        location.longitude,
        result.display_name,
    )


def _decimal_miles(miles: float) -> Decimal:
    return Decimal(str(miles)).quantize(MILES)


def plan_route(start: LocationInput, finish: LocationInput, include_all_stations: bool = False) -> dict:
    started = time.perf_counter()
    origin = resolve_location(start, "start")
    destination = resolve_location(finish, "finish")
    if haversine_miles(*origin.point, *destination.point) < SAME_LOCATION_MILES:
        raise SameLocationError()

    route, route_cached = get_route(origin.point, destination.point)
    corridor = settings.FUEL_STATION_ROUTE_CORRIDOR_MILES
    index = RouteIndex(route.coordinates, corridor, route.distance_miles)
    candidates = find_candidate_stations(index)

    mpg = Decimal(settings.VEHICLE_MPG)
    tank = Decimal(settings.VEHICLE_MAX_RANGE_MILES) / mpg
    options = [FuelOption(_decimal_miles(c.route_miles), c.price, c) for c in candidates]
    try:
        plan = plan_fuel_stops(
            options,
            _decimal_miles(route.distance_miles),
            tank_gallons=tank,
            mpg=mpg,
            stop_penalty=settings.FUEL_STOP_PENALTY_DOLLARS,
        )
    except InfeasibleFuelPlanError as exc:
        raise NoFeasibleFuelPlanError(
            f"No fuel station within {settings.VEHICLE_MAX_RANGE_MILES} miles of route mile "
            f"{exc.stranded_at_miles:.1f}, so the trip cannot be completed."
        ) from exc

    logger.info(
        "event=route_planned distance_miles=%.1f candidates=%d stops=%d total_cost=%s route_cached=%s elapsed_ms=%.0f",
        route.distance_miles,
        len(candidates),
        len(plan.stops),
        plan.total_cost,
        route_cached,
        (time.perf_counter() - started) * 1000,
    )
    return build_response(origin, destination, route, candidates, plan, tank, mpg, route_cached, include_all_stations)


def _station_payload(station: CandidateStation) -> dict:
    return {
        "id": station.id,
        "opis_id": station.opis_id,
        "name": station.name,
        "address": station.address,
        "city": station.city,
        "state": station.state,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "price_per_gallon": station.price.normalize(),
    }


def _location_payload(location: ResolvedLocation) -> dict:
    return {
        "input": location.input,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "display_name": location.display_name,
    }


def _point(lat: float, lon: float, properties: dict) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": properties}


def build_response(
    origin: ResolvedLocation,
    destination: ResolvedLocation,
    route: Route,
    candidates: list[CandidateStation],
    plan: FuelPlan,
    tank: Decimal,
    mpg: Decimal,
    route_cached: bool,
    include_all_stations: bool = False,
) -> dict:
    fuel_stops = []
    previous_miles = Decimal(0)
    for sequence, stop in enumerate(plan.stops, start=1):
        station: CandidateStation = stop.option.ref
        fuel_stops.append(
            {
                "sequence": sequence,
                "station": _station_payload(station),
                "route_distance_miles": stop.option.route_miles,
                "distance_from_previous_stop_miles": stop.option.route_miles - previous_miles,
                "distance_from_route_miles": round(station.offset_miles, 2),
                "fuel_before_stop_gallons": stop.fuel_before_gallons.quantize(GALLONS),
                "fuel_purchased_gallons": stop.gallons.quantize(GALLONS),
                "fuel_cost": stop.cost,
            }
        )
        previous_miles = stop.option.route_miles

    sequence_by_station = {stop["station"]["id"]: stop["sequence"] for stop in fuel_stops}
    station_features = [
        _point(
            c.latitude,
            c.longitude,
            {
                "marker": "fuel_station",
                "id": c.id,
                "name": c.name,
                "price_per_gallon": c.price.normalize(),
                "route_distance_miles": round(c.route_miles, 1),
                "selected": c.id in sequence_by_station,
                "sequence": sequence_by_station.get(c.id),
            },
        )
        for c in candidates
        if include_all_stations or c.id in sequence_by_station
    ]

    return {
        "start": _location_payload(origin),
        "finish": _location_payload(destination),
        "route": {
            "distance_miles": round(route.distance_miles, 2),
            "duration_minutes": round(route.duration_seconds / 60, 1),
            "geometry": {"type": "LineString", "coordinates": route.display_coordinates},
        },
        "vehicle": {
            "max_range_miles": settings.VEHICLE_MAX_RANGE_MILES,
            "fuel_efficiency_mpg": mpg,
            "tank_capacity_gallons": tank,
        },
        "fuel": {
            "total_fuel_required_gallons": plan.fuel_required_gallons.quantize(GALLONS),
            "initial_fuel_gallons": plan.initial_fuel_gallons.quantize(GALLONS),
            "fuel_purchased_gallons": plan.fuel_purchased_gallons.quantize(GALLONS),
            "fuel_at_destination_gallons": plan.fuel_at_destination_gallons.quantize(GALLONS),
            "total_cost": plan.total_cost,
            "currency": "USD",
            "cost_assumption": COST_ASSUMPTION,
        },
        "fuel_stops": fuel_stops,
        "map": {
            "type": "FeatureCollection",
            "features": [
                _point(origin.latitude, origin.longitude, {"marker": "start", "label": str(origin.input)}),
                _point(
                    destination.latitude, destination.longitude, {"marker": "finish", "label": str(destination.input)}
                ),
                *station_features,
            ],
        },
        "meta": {
            "corridor_miles": settings.FUEL_STATION_ROUTE_CORRIDOR_MILES,
            "stop_penalty_dollars": settings.FUEL_STOP_PENALTY_DOLLARS,
            "candidate_station_count": len(candidates),
            "route_cached": route_cached,
        },
    }
