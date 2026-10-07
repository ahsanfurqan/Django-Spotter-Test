"""Picks where to refuel along a route.

Minimises fuel cost plus a fixed penalty per stop, so the plan doesn't stop twice in 20 miles to save
a few cents. It's an exact dynamic program built on one rule from Khuller, Malekian and Mestre
("To fill or not to fill", 2007): between two consecutive stops you either fill the tank, when the
next stop is pricier, or buy just enough to reach it, when it's cheaper. That means the fuel left on
arrival at any station can only take a few values, which keeps the search small.

Everything here is Decimal. Positions are stored in gallons (miles / mpg), which makes the tank size
also the longest leg the vehicle can drive.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple

ZERO = Decimal(0)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class FuelOption:
    route_miles: Decimal
    price: Decimal
    ref: Any = None


@dataclass(frozen=True)
class FuelStop:
    option: FuelOption
    fuel_before_gallons: Decimal
    gallons: Decimal
    cost: Decimal


@dataclass(frozen=True)
class FuelPlan:
    stops: list[FuelStop]
    fuel_required_gallons: Decimal
    initial_fuel_gallons: Decimal
    fuel_purchased_gallons: Decimal
    fuel_at_destination_gallons: Decimal
    total_cost: Decimal


class InfeasibleFuelPlanError(Exception):
    def __init__(self, stranded_at_miles: Decimal):
        self.stranded_at_miles = stranded_at_miles
        super().__init__(f"No fuel station reachable after route mile {stranded_at_miles}.")


class _Label(NamedTuple):
    objective: Decimal  # fuel cost + penalties so far
    stops: int
    previous: tuple[int, Decimal, Decimal] | None  # (node, arrival fuel at node, gallons bought there)


def money(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def _cheapest_per_position(options: list[FuelOption]) -> list[FuelOption]:
    """Stations at the same route mile (e.g. same city) are interchangeable: keep the cheapest."""
    best: dict[Decimal, FuelOption] = {}
    for option in options:
        current = best.get(option.route_miles)
        if current is None or option.price < current.price:
            best[option.route_miles] = option
    return sorted(best.values(), key=lambda o: o.route_miles)


def plan_fuel_stops(
    options: Sequence[FuelOption],
    trip_miles: Decimal,
    *,
    tank_gallons: Decimal,
    mpg: Decimal,
    stop_penalty: Decimal = ZERO,
) -> FuelPlan:
    if trip_miles <= 0:
        raise ValueError("Trip distance must be positive.")
    tank, mpg, stop_penalty = Decimal(tank_gallons), Decimal(mpg), Decimal(stop_penalty)
    required = trip_miles / mpg
    if required <= tank:
        # The starting tank already covers the trip, so buying anything would only add cost.
        # Short trips are also where the DP is slowest (every station is in range of every
        # other one), so it's worth skipping.
        return FuelPlan([], required, tank, ZERO, tank - required, ZERO)
    stations = _cheapest_per_position([o for o in options if ZERO <= o.route_miles < trip_miles])

    positions = [ZERO, *(o.route_miles / mpg for o in stations), trip_miles / mpg]
    prices = [ZERO, *(o.price for o in stations)]
    destination = len(positions) - 1

    # labels[node][fuel on arrival] holds the cheapest way found so far to get there with that much
    # fuel. Nodes are handled in route order, so by the time we reach one it can't improve any more.
    # Between two stops we either fill up (next one is pricier) or buy just enough to get there
    # (next one is cheaper). That keeps the number of fuel levels per station small.
    labels: list[dict[Decimal, _Label]] = [{} for _ in positions]
    labels[0][tank] = _Label(ZERO, 0, None)
    for u in range(destination):
        for fuel, label in labels[u].items():
            w = u + 1
            while w <= destination and (gap := positions[w] - positions[u]) <= tank:
                if w == destination:
                    buy = max(ZERO, gap - fuel)
                elif prices[w] > prices[u]:
                    buy = tank - fuel
                elif fuel <= gap:
                    buy = gap - fuel
                else:
                    w += 1  # we'd arrive with fuel to spare, so stopping at u wasn't needed
                    continue
                stopped = buy > 0
                candidate = _Label(
                    label.objective + buy * prices[u] + (stop_penalty if stopped else ZERO),
                    label.stops + stopped,
                    (u, fuel, buy),
                )
                arrival = fuel + buy - gap
                incumbent = labels[w].get(arrival)
                if incumbent is None or candidate[:2] < incumbent[:2]:
                    labels[w][arrival] = candidate
                w += 1

    if not labels[destination]:
        stranded = max(node for node in range(destination) if labels[node])
        raise InfeasibleFuelPlanError(positions[stranded] * mpg)

    arrival_fuel, label = min(labels[destination].items(), key=lambda item: item[1][:2])
    stops: list[FuelStop] = []
    while label.previous is not None:
        node, fuel, buy = label.previous
        if buy > 0:
            stops.append(FuelStop(stations[node - 1], fuel, buy, money(buy * prices[node])))
        label = labels[node][fuel]
    stops.reverse()

    return FuelPlan(
        stops=stops,
        fuel_required_gallons=positions[destination],
        initial_fuel_gallons=tank,
        fuel_purchased_gallons=sum((s.gallons for s in stops), ZERO),
        fuel_at_destination_gallons=arrival_fuel,
        total_cost=sum((s.cost for s in stops), ZERO),
    )
