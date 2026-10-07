from dataclasses import dataclass
from decimal import Decimal

from fuel.models import FuelStation

from .geo import RouteIndex


@dataclass(frozen=True)
class CandidateStation:
    id: int
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    latitude: float
    longitude: float
    price: Decimal
    route_miles: float
    offset_miles: float


def find_candidate_stations(index: RouteIndex) -> list[CandidateStation]:
    """Geocoded US stations within the route corridor, ordered by position along the route.

    One indexed bounding-box query narrows the table, then each row is projected onto the route
    locally. Stations without coordinates are simply never candidates.
    """
    min_lat, max_lat, min_lon, max_lon = index.bounding_box()
    rows = FuelStation.objects.filter(
        country="US",
        geocode_status=FuelStation.GeocodeStatus.OK,
        latitude__range=(min_lat, max_lat),
        longitude__range=(min_lon, max_lon),
    ).values_list("id", "opis_id", "name", "address", "city", "state", "latitude", "longitude", "retail_price")

    candidates = []
    for station_id, opis_id, name, address, city, state, lat, lon, price in rows:
        position = index.locate(lat, lon)
        if position is None:
            continue
        candidates.append(
            CandidateStation(
                id=station_id,
                opis_id=opis_id,
                name=name,
                address=address,
                city=city,
                state=state,
                latitude=lat,
                longitude=lon,
                price=price,
                route_miles=position.route_miles,
                offset_miles=position.offset_miles,
            )
        )
    candidates.sort(key=lambda c: (c.route_miles, c.price))
    return candidates
