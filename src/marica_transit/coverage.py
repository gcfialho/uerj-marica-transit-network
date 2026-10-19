"""Stop-spacing (coverage) analysis along a single road.

Case study from the course project: are the stops along Avenida Carlos Marighella, the
main road of Itaipuaçu, spaced closely enough? Stops are matched by street name, ordered
along the road and every gap above a threshold gets a suggested new stop at its midpoint.
"""

from __future__ import annotations

import itertools
import math
import unicodedata
from dataclasses import dataclass

import pandas as pd

from marica_transit.data import Quality
from marica_transit.geo import haversine_km

# Spellings found in the data, plus the road's former name.
CARLOS_MARIGHELLA = ("Carlos Marighella", "Carlos Mariguella", "Vitória Régia")

# (upper bound in metres, label, colour) used for both the report and the map.
SPACING_CLASSES: tuple[tuple[float, str, str], ...] = (
    (400, "near (<=400 m)", "green"),
    (600, "reasonable (400-600 m)", "gold"),
    (800, "medium (600-800 m)", "orange"),
    (1200, "far (800-1200 m)", "red"),
    (math.inf, "very far (>1200 m)", "purple"),
)


def classify_spacing(distance_m: float) -> tuple[str, str]:
    """Return ``(label, colour)`` for a stop-to-stop spacing in metres."""
    for upper, label, colour in SPACING_CLASSES:
        if distance_m <= upper:
            return label, colour
    raise AssertionError("unreachable")


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def match_street(stops: pd.DataFrame, street_names: tuple[str, ...]) -> pd.DataFrame:
    """Rows whose Moovit stop name *or* geocoded address mentions one of ``street_names``.

    Matching is accent- and case-insensitive. Out-of-area geocodes are excluded.
    """
    keys = [_normalise(s) for s in street_names]
    text = (stops["stop_name"].fillna("") + " | " + stops["geocoded_address"].fillna("")).map(
        _normalise
    )
    mask = text.map(lambda t: any(k in t for k in keys))
    usable = ~stops["geocode_quality"].isin([Quality.OUT_OF_AREA, Quality.MISSING])
    return stops[mask & usable]


@dataclass(frozen=True, slots=True)
class RoadPoint:
    lat: float
    lon: float
    stop_names: tuple[str, ...]
    lines: tuple[str, ...]
    approximate: bool


@dataclass(frozen=True, slots=True)
class Gap:
    start: RoadPoint
    end: RoadPoint
    distance_m: float

    @property
    def midpoint(self) -> tuple[float, float]:
        return (self.start.lat + self.end.lat) / 2, (self.start.lon + self.end.lon) / 2


@dataclass(frozen=True, slots=True)
class CoverageResult:
    points: list[RoadPoint]  # ordered along the road
    spacings_m: list[float]  # spacings_m[i] is between points[i] and points[i + 1]
    max_gap_m: float

    @property
    def gaps(self) -> list[Gap]:
        return [
            Gap(self.points[i], self.points[i + 1], d)
            for i, d in enumerate(self.spacings_m)
            if d > self.max_gap_m
        ]

    @property
    def road_length_km(self) -> float:
        return sum(self.spacings_m) / 1000

    def class_counts(self) -> dict[str, int]:
        counts = {label: 0 for _, label, _ in SPACING_CLASSES}
        for d in self.spacings_m:
            counts[classify_spacing(d)[0]] += 1
        return counts


def _group_points(rows: pd.DataFrame) -> list[RoadPoint]:
    points = []
    for (lat, lon), grp in rows.groupby(["lat", "lon"], sort=True):
        points.append(
            RoadPoint(
                lat=float(lat),
                lon=float(lon),
                stop_names=tuple(sorted(grp["stop_name"].unique())),
                lines=tuple(sorted(grp["line_code"].unique())),
                approximate=bool((grp["geocode_quality"] == Quality.APPROXIMATE).any()),
            )
        )
    return points


def order_along_road(points: list[RoadPoint]) -> list[RoadPoint]:
    """Order points into a chain that follows the road.

    Start from the extreme point along the road's principal axis, then repeatedly hop to
    the nearest unvisited point (great-circle distance). The original version started at an
    arbitrary point and measured nearest neighbours in raw degrees, which overstates
    east-west distances by ~9% at this latitude and could start the chain mid-road.
    """
    if len(points) < 3:
        return sorted(points, key=lambda p: (p.lon, p.lat))
    mean_lat = sum(p.lat for p in points) / len(points)
    mean_lon = sum(p.lon for p in points) / len(points)
    kx = math.cos(math.radians(mean_lat))  # local equirectangular projection
    xs = [(p.lon - mean_lon) * kx for p in points]
    ys = [p.lat - mean_lat for p in points]
    sxx = sum(x * x for x in xs)
    syy = sum(y * y for y in ys)
    sxy = sum(x * y for x, y in zip(xs, ys, strict=True))
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)  # principal axis of the point cloud
    proj = [x * math.cos(angle) + y * math.sin(angle) for x, y in zip(xs, ys, strict=True)]

    remaining = list(range(len(points)))
    current = min(remaining, key=lambda i: proj[i])
    chain = [current]
    remaining.remove(current)
    while remaining:
        cur = points[current]
        current = min(
            remaining, key=lambda i: haversine_km(cur.lat, cur.lon, points[i].lat, points[i].lon)
        )
        chain.append(current)
        remaining.remove(current)
    return [points[i] for i in chain]


def analyse_road(
    stops: pd.DataFrame,
    street_names: tuple[str, ...] = CARLOS_MARIGHELLA,
    max_gap_m: float = 450.0,
) -> CoverageResult:
    """Run the stop-spacing analysis for one road."""
    rows = match_street(stops, street_names)
    if rows.empty:
        raise ValueError(f"no stops found on {street_names}")
    points = order_along_road(_group_points(rows))
    spacings = [
        haversine_km(a.lat, a.lon, b.lat, b.lon) * 1000 for a, b in itertools.pairwise(points)
    ]
    return CoverageResult(points=points, spacings_m=spacings, max_gap_m=max_gap_m)
