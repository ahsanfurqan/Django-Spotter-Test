import math
import random

import pytest

from conftest import ROUTE_COORDS, ROUTE_LAT, ROUTE_MILES, lon_at_mile
from routes.services.geo import MILES_PER_DEGREE, RouteIndex, haversine_miles, simplify_line


def test_haversine_known_distance():
    # New York City -> Los Angeles great-circle distance is ~2,445 miles.
    assert haversine_miles(40.7128, -74.0060, 34.0522, -118.2437) == pytest.approx(2445, abs=5)
    assert haversine_miles(40, -100, 40, -100) == 0


def test_cumulative_distance_is_scaled_to_router_distance():
    index = RouteIndex(ROUTE_COORDS, 10, total_miles=ROUTE_MILES * 1.02)
    assert index.total_miles == pytest.approx(ROUTE_MILES * 1.02)
    assert index.cumulative[0] == 0


def test_locate_projects_point_onto_route():
    index = RouteIndex(ROUTE_COORDS, 10, total_miles=ROUTE_MILES)
    offset_degrees = 5 / MILES_PER_DEGREE  # 5 miles north of the road
    position = index.locate(ROUTE_LAT + offset_degrees, lon_at_mile(300))
    assert position is not None
    assert position.offset_miles == pytest.approx(5, abs=0.05)
    assert position.route_miles == pytest.approx(300, abs=1)


def test_locate_rejects_points_outside_corridor():
    index = RouteIndex(ROUTE_COORDS, 10, total_miles=ROUTE_MILES)
    assert index.locate(ROUTE_LAT + 11 / MILES_PER_DEGREE, lon_at_mile(300)) is None
    assert index.locate(ROUTE_LAT, -60.0) is None  # far beyond the end of the route


def test_locate_beyond_route_ends_clamps_to_endpoints():
    index = RouteIndex(ROUTE_COORDS, 10, total_miles=ROUTE_MILES)
    position = index.locate(ROUTE_LAT, -100.05)
    assert position.route_miles == 0
    assert position.offset_miles == pytest.approx(0.05 * MILES_PER_DEGREE * math.cos(math.radians(40)), rel=0.01)


def test_bounding_box_covers_route_plus_corridor():
    index = RouteIndex(ROUTE_COORDS, 10)
    min_lat, max_lat, min_lon, max_lon = index.bounding_box()
    assert min_lat <= ROUTE_LAT - 10 / MILES_PER_DEGREE and max_lat >= ROUTE_LAT + 10 / MILES_PER_DEGREE
    assert min_lon < -100 and max_lon > -80


def test_grid_lookup_matches_brute_force_on_winding_route():
    rng = random.Random(7)
    coords, lat, lon = [], 35.0, -110.0
    for _ in range(400):  # a random wiggly route, ~0.05 degree steps
        lat += rng.uniform(-0.05, 0.05)
        lon += rng.uniform(0.0, 0.06)
        coords.append([lon, lat])
    index = RouteIndex(coords, 15)

    def brute(point_lat, point_lon):
        kx = MILES_PER_DEGREE * math.cos(math.radians(point_lat))
        best = math.inf
        for (alon, alat), (blon, blat) in zip(coords, coords[1:], strict=False):
            ax, ay = (alon - point_lon) * kx, (alat - point_lat) * MILES_PER_DEGREE
            bx, by = (blon - point_lon) * kx, (blat - point_lat) * MILES_PER_DEGREE
            dx, dy = bx - ax, by - ay
            t = min(1, max(0, -(ax * dx + ay * dy) / (dx * dx + dy * dy or 1)))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
        return best

    for _ in range(300):
        p_lat, p_lon = rng.uniform(34, 37), rng.uniform(-110.5, -97)
        expected = brute(p_lat, p_lon)
        position = index.locate(p_lat, p_lon)
        if expected <= 15:
            assert position is not None
            assert position.offset_miles == pytest.approx(expected, abs=1e-9)
        else:
            assert position is None


def test_route_needs_two_points():
    with pytest.raises(ValueError):
        RouteIndex([[0, 0]], 10)


def test_simplify_drops_collinear_points_and_keeps_corners():
    line = [[0, 0], [1, 0], [2, 0], [2, 1], [2, 2]]
    assert simplify_line(line, 0.01) == [[0, 0], [2, 0], [2, 2]]
    assert simplify_line(line[:2], 0.01) == line[:2]


def test_simplified_line_stays_within_tolerance():
    rng = random.Random(3)
    line, lat, lon = [], 35.0, -110.0
    for _ in range(2000):
        lat += rng.uniform(-0.0002, 0.0002)  # road-like: wiggles smaller than the tolerance
        lon += rng.uniform(0, 0.003)
        line.append([lon, lat])
    tolerance = 0.0005
    simplified = simplify_line(line, tolerance)
    assert simplified[0] == line[0] and simplified[-1] == line[-1]
    assert len(simplified) < len(line) / 2

    def distance_to_polyline(px, py):
        best = math.inf
        for (ax, ay), (bx, by) in zip(simplified, simplified[1:], strict=False):
            dx, dy = bx - ax, by - ay
            t = min(1, max(0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
            best = min(best, math.hypot(ax + t * dx - px, ay + t * dy - py))
        return best

    assert max(distance_to_polyline(x, y) for x, y in line) <= tolerance + 1e-12
