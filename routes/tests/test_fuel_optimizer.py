import random
from decimal import Decimal

import pytest

from routes.services.fuel_optimizer import FuelOption, InfeasibleFuelPlanError, money, plan_fuel_stops

TANK = Decimal(50)
MPG = Decimal(10)


def plan(stations: list[tuple[int | str, str]], trip_miles, tank=TANK, mpg=MPG, penalty="0"):
    options = [FuelOption(Decimal(str(miles)), Decimal(price), ref=(miles, price)) for miles, price in stations]
    return plan_fuel_stops(options, Decimal(str(trip_miles)), tank_gallons=tank, mpg=mpg, stop_penalty=Decimal(penalty))


def stop_summary(result):
    return [(s.option.route_miles, s.gallons, s.cost) for s in result.stops]


def test_trip_within_range_needs_no_fuel():
    result = plan([(100, "3.00"), (200, "2.50")], 420)
    assert result.stops == []
    assert result.total_cost == Decimal(0)
    assert result.fuel_required_gallons == Decimal(42)
    assert result.fuel_purchased_gallons == Decimal(0)
    assert result.fuel_at_destination_gallons == Decimal(8)


def test_short_trip_through_dense_cheap_stations_buys_nothing():
    stations = [(m, "2.00") for m in range(1, 500)]  # cheap fuel everywhere, ~500 stations
    result = plan(stations, 499, penalty="0")
    assert result.stops == []
    assert result.total_cost == Decimal(0)
    assert result.fuel_at_destination_gallons == Decimal("0.1")


def test_trip_of_exactly_max_range_needs_no_stop():
    result = plan([], 500)
    assert result.stops == []
    assert result.fuel_at_destination_gallons == Decimal(0)


def test_trip_just_over_range_without_stations_is_infeasible():
    with pytest.raises(InfeasibleFuelPlanError) as exc:
        plan([], "500.001")
    assert exc.value.stranded_at_miles == Decimal(0)


def test_station_exactly_at_max_range_is_reachable():
    result = plan([(500, "4.00")], 1000)
    assert stop_summary(result) == [(Decimal(500), Decimal(50), Decimal("200.00"))]
    assert result.stops[0].fuel_before_gallons == Decimal(0)


def test_station_just_beyond_max_range_is_not_reachable():
    with pytest.raises(InfeasibleFuelPlanError):
        plan([("500.1", "4.00")], 1000)


def test_1000_mile_trip_buys_exactly_the_missing_50_gallons():
    result = plan([(400, "3.00"), (600, "3.50")], 1000)
    # Fill the tank at the cheaper $3.00 stop, then top up only the remainder at $3.50.
    assert stop_summary(result) == [
        (Decimal(400), Decimal(40), Decimal("120.00")),
        (Decimal(600), Decimal(10), Decimal("35.00")),
    ]
    assert result.fuel_required_gallons == Decimal(100)
    assert result.fuel_purchased_gallons == Decimal(50)
    assert result.fuel_at_destination_gallons == Decimal(0)
    assert result.total_cost == Decimal("155.00")


def test_infeasible_when_last_gap_exceeds_range():
    with pytest.raises(InfeasibleFuelPlanError) as exc:
        plan([(400, "3.00")], 1000)
    assert exc.value.stranded_at_miles == Decimal(400)


def test_does_not_simply_pick_globally_cheapest_station():
    # The $2.50 station at mile 700 is out of range from the start; the plan must use mile 450 first.
    result = plan([(450, "4.00"), (700, "2.50")], 1100)
    assert stop_summary(result) == [
        (Decimal(450), Decimal(20), Decimal("80.00")),  # arrive with 5 gal; buy just enough for 250 miles
        (Decimal(700), Decimal(40), Decimal("100.00")),  # 400 miles remain
    ]
    assert result.total_cost == Decimal("180.00")


def test_expensive_station_before_cheaper_one_is_skipped():
    result = plan([(300, "5.00"), (450, "3.00")], 900)
    assert stop_summary(result) == [(Decimal(450), Decimal(40), Decimal("120.00"))]


def test_fills_up_at_cheap_station_before_expensive_stretch():
    result = plan([(100, "2.00"), (400, "4.00")], 800)
    # Fill at $2 (10 gal), then top up only what is still needed at $4 (20 gal).
    assert stop_summary(result) == [
        (Decimal(100), Decimal(10), Decimal("20.00")),
        (Decimal(400), Decimal(20), Decimal("80.00")),
    ]
    assert result.total_cost == Decimal("100.00")


def test_multiple_stops_on_long_trip_respect_range():
    stations = [(m, "3.50") for m in range(250, 2500, 250)]
    result = plan(stations, 2400)
    assert len(result.stops) >= 4
    previous = Decimal(0)
    for stop in result.stops:
        assert stop.option.route_miles - previous <= 500
        previous = stop.option.route_miles
    assert Decimal(2400) - previous <= 500
    assert result.fuel_purchased_gallons == Decimal(190)
    assert result.total_cost == Decimal("665.00")


def test_equal_prices_prefer_farthest_station_to_save_stops():
    result = plan([(200, "3.00"), (300, "3.00"), (480, "3.00")], 900)
    assert [s.option.route_miles for s in result.stops] == [Decimal(480)]


def test_duplicate_stations_at_same_position_use_cheapest():
    result = plan([(450, "3.90"), (450, "3.10"), (450, "3.50")], 700)
    assert [(s.option.price, s.gallons) for s in result.stops] == [(Decimal("3.10"), Decimal(20))]


def test_stations_beyond_destination_are_ignored():
    result = plan([(400, "3.00"), (900, "1.00")], 800)
    assert [s.option.route_miles for s in result.stops] == [Decimal(400)]


def test_purchase_amounts_and_costs_are_decimals():
    result = plan([(450, "3.00733333")], 700)
    stop = result.stops[0]
    assert isinstance(stop.gallons, Decimal) and isinstance(stop.cost, Decimal)
    assert stop.gallons == Decimal(20)  # arrive with 5 gal, 250 miles to go
    assert stop.cost == Decimal("60.15")  # 60.1466666 rounded half-up to cents
    assert result.total_cost == stop.cost


@pytest.mark.parametrize(
    ("penalty", "expected_stops", "expected_cost"),
    [
        ("0", [100, 450], "115.00"),  # 10 gal at $2.50 + 30 gal at $3.00
        ("4", [100, 450], "115.00"),  # the extra stop saves $5 > $4
        ("6", [450], "120.00"),  # the extra stop saves $5 < $6: one stop, 40 gal at $3.00
    ],
)
def test_stop_penalty_trades_small_savings_for_fewer_stops(penalty, expected_stops, expected_cost):
    result = plan([(100, "2.50"), (450, "3.00")], 900, penalty=penalty)
    assert [s.option.route_miles for s in result.stops] == [Decimal(m) for m in expected_stops]
    assert result.total_cost == Decimal(expected_cost)
    assert result.fuel_purchased_gallons == Decimal(40)


def test_stop_penalty_never_breaks_feasibility():
    result = plan([(250, "9.99"), (500, "3.00"), (750, "9.99")], 1000, penalty="1000")
    assert [s.option.route_miles for s in result.stops] == [Decimal(500)]


def test_money_rounds_half_up():
    assert money(Decimal("1.005")) == Decimal("1.01")
    assert money(Decimal("1.0049")) == Decimal("1.00")


def test_rejects_non_positive_trip():
    with pytest.raises(ValueError):
        plan([], 0)


# --- exhaustive cross-check ------------------------------------------------------------------


def brute_force_cost(stations, trip, tank, mpg, penalty=Decimal(0)):
    """Dynamic program over integer fuel levels at every station (exact when distances are whole gallons)."""
    nodes = sorted((Decimal(m) / mpg, Decimal(p)) for m, p in stations if Decimal(m) < trip)
    points = [(Decimal(0), None), *nodes, (Decimal(trip) / mpg, None)]
    tank = int(tank)
    best = {tank: Decimal(0)}  # fuel on arrival at current point -> min cost
    for k in range(len(points) - 1):
        gap = points[k + 1][0] - points[k][0]
        price = points[k][1]
        nxt: dict[int, Decimal] = {}
        for fuel, cost in best.items():
            purchases = range(0, tank - fuel + 1) if price is not None else [0]
            for buy in purchases:
                remaining = fuel + buy - gap
                if remaining < 0:
                    continue
                total = cost + (price or 0) * buy + (penalty if buy and price is not None else 0)
                key = int(remaining)
                if key not in nxt or total < nxt[key]:
                    nxt[key] = total
        best = nxt
        if not best:
            return None
    return min(best.values())


@pytest.mark.parametrize("seed", range(300))
def test_dp_matches_exhaustive_optimum(seed):
    rng = random.Random(seed)
    tank, mpg = Decimal(5), Decimal(10)  # 50-mile range keeps the brute force small
    trip = rng.randrange(20, 200, 10)
    stations = [
        (rng.randrange(0, trip + 30, 10), rng.choice(["2.90", "3.00", "3.10", "3.25", "3.60", "4.10"]))
        for _ in range(rng.randrange(0, 14))
    ]
    penalty = Decimal(rng.choice(["0", "0", "0.5", "2", "6"]))
    expected = brute_force_cost(stations, trip, tank, mpg, penalty)
    if expected is None:
        with pytest.raises(InfeasibleFuelPlanError):
            plan(stations, trip, tank, mpg, penalty)
        return
    result = plan(stations, trip, tank, mpg, penalty)
    objective = sum((s.gallons * s.option.price + penalty for s in result.stops), Decimal(0))
    assert objective == expected
    assert result.fuel_purchased_gallons == max(Decimal(0), Decimal(trip) / mpg - tank)
