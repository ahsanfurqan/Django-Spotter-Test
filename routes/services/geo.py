"""Local geometry: distances and projecting points onto a route polyline without external calls."""

import math
from collections import defaultdict
from dataclasses import dataclass

EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344
MILES_PER_DEGREE = EARTH_RADIUS_MILES * math.pi / 180  # ~69.09 miles per degree of latitude


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class RoutePosition:
    route_miles: float  # distance from the start, measured along the route
    offset_miles: float  # straight-line distance from the point to the route


class RouteIndex:
    """A grid over the route's segments, so a station is only compared with segments close to it.

    A coast-to-coast route has tens of thousands of points, and checking every station against every
    segment is slow. Each grid cell is at least as wide as the corridor, so any segment close enough
    to a station has to touch the station's cell or one of the 8 cells around it.
    """

    def __init__(self, coordinates: list[list[float]], max_offset_miles: float, total_miles: float | None = None):
        if len(coordinates) < 2:
            raise ValueError("A route needs at least two coordinates.")
        self.lons = [c[0] for c in coordinates]
        self.lats = [c[1] for c in coordinates]
        self.max_offset_miles = max_offset_miles

        cumulative = [0.0]
        for i in range(1, len(coordinates)):
            cumulative.append(
                cumulative[-1] + haversine_miles(self.lats[i - 1], self.lons[i - 1], self.lats[i], self.lons[i])
            )
        # Scale to the router's distance so station positions and trip length use the same ruler.
        scale = total_miles / cumulative[-1] if total_miles and cumulative[-1] > 0 else 1.0
        self.cumulative = [d * scale for d in cumulative]
        self.total_miles = self.cumulative[-1]

        widest_lat = max(abs(min(self.lats)), abs(max(self.lats))) + max_offset_miles / MILES_PER_DEGREE
        miles_per_lon_degree = MILES_PER_DEGREE * max(math.cos(math.radians(min(widest_lat, 89.0))), 0.01)
        self.cell_degrees = max(max_offset_miles / miles_per_lon_degree, 1e-4)
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i in range(len(coordinates) - 1):
            x0, x1 = sorted((self._cell(self.lons[i]), self._cell(self.lons[i + 1])))
            y0, y1 = sorted((self._cell(self.lats[i]), self._cell(self.lats[i + 1])))
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    self.grid[(x, y)].append(i)

    def _cell(self, degrees: float) -> int:
        return math.floor(degrees / self.cell_degrees)

    def bounding_box(self) -> tuple[float, float, float, float]:
        """(min_lat, max_lat, min_lon, max_lon) of the route, padded by the corridor width."""
        pad = self.cell_degrees  # >= corridor width in degrees for both axes
        return min(self.lats) - pad, max(self.lats) + pad, min(self.lons) - pad, max(self.lons) + pad

    def locate(self, lat: float, lon: float) -> RoutePosition | None:
        """Project a point onto the nearest route segment, or None if it is outside the corridor."""
        cx, cy = self._cell(lon), self._cell(lat)
        segments = {i for dx in (-1, 0, 1) for dy in (-1, 0, 1) for i in self.grid.get((cx + dx, cy + dy), ())}
        if not segments:
            return None

        # Treat the area around the point as flat. Over 10-20 miles the error is well under 1%.
        kx = MILES_PER_DEGREE * math.cos(math.radians(lat))
        ky = MILES_PER_DEGREE
        best: tuple[float, float] | None = None
        for i in segments:
            ax, ay = (self.lons[i] - lon) * kx, (self.lats[i] - lat) * ky
            bx, by = (self.lons[i + 1] - lon) * kx, (self.lats[i + 1] - lat) * ky
            dx, dy = bx - ax, by - ay
            length_sq = dx * dx + dy * dy
            t = 0.0 if length_sq == 0 else min(1.0, max(0.0, -(ax * dx + ay * dy) / length_sq))
            offset = math.hypot(ax + t * dx, ay + t * dy)
            if best is None or offset < best[0]:
                along = self.cumulative[i] + t * (self.cumulative[i + 1] - self.cumulative[i])
                best = (offset, along)

        if best is None or best[0] > self.max_offset_miles:
            return None
        return RoutePosition(route_miles=best[1], offset_miles=best[0])


def simplify_line(coordinates: list[list[float]], tolerance_degrees: float) -> list[list[float]]:
    """Remove points that barely change the shape of the line (Douglas-Peucker).

    No point of the original line ends up further than `tolerance_degrees` from the result. Distances
    are measured in plain degrees, which is fine at this scale. Written with a stack rather than
    recursion so long routes can't hit Python's recursion limit.
    """
    n = len(coordinates)
    if n < 3:
        return list(coordinates)
    keep = [False] * n
    keep[0] = keep[-1] = True
    tolerance_sq = tolerance_degrees * tolerance_degrees
    stack = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = coordinates[first], coordinates[last]
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        worst, worst_index = -1.0, -1
        for i in range(first + 1, last):
            px, py = coordinates[i]
            t = 0.0 if length_sq == 0 else min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
            error = (ax + t * dx - px) ** 2 + (ay + t * dy - py) ** 2
            if error > worst:
                worst, worst_index = error, i
        if worst > tolerance_sq:
            keep[worst_index] = True
            stack += [(first, worst_index), (worst_index, last)]
    return [point for point, kept in zip(coordinates, keep, strict=True) if kept]
