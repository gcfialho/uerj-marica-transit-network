import pandas as pd
import pytest

from marica_transit.coverage import (
    RoadPoint,
    analyse_road,
    classify_spacing,
    match_street,
    order_along_road,
)
from marica_transit.data import Quality


@pytest.mark.parametrize(
    ("metres", "colour"),
    [(0, "green"), (400, "green"), (401, "gold"), (650, "orange"), (1000, "red"), (5000, "purple")],
)
def test_classify_spacing(metres: float, colour: str) -> None:
    assert classify_spacing(metres)[1] == colour


def test_match_street_is_accent_and_case_insensitive(stops: pd.DataFrame) -> None:
    matched = match_street(stops, ("CARLOS MARIGHELLA",))
    assert set(matched["stop_name"]) == {"Delta"}
    assert match_street(stops, ("Vitoria Regia",)).empty


def test_match_street_skips_out_of_area(stops: pd.DataFrame) -> None:
    assert match_street(stops, ("Bambuí",)).empty


def _road(n: int, step_deg: float = 0.004) -> pd.DataFrame:
    """n stops on a straight NE-running road, deliberately listed out of order."""
    order = sorted(range(n), key=lambda i: (i * 7) % n)  # a fixed shuffle of 0..n-1
    rows = [
        {
            "line_code": "X",
            "stop_name": f"Avenida Teste, {i}",
            "geocoded_address": "Avenida Teste",
            "lat": -22.95 + i * step_deg,
            "lon": -42.98 + i * step_deg,
            "geocode_quality": Quality.OK,
        }
        for i in order
    ]
    return pd.DataFrame(rows)


def test_order_along_road_recovers_sequence() -> None:
    df = _road(5)
    points = [
        RoadPoint(r.lat, r.lon, (r.stop_name,), ("X",), approximate=False) for r in df.itertuples()
    ]
    ordered = [p.stop_names[0] for p in order_along_road(points)]
    expected = [f"Avenida Teste, {i}" for i in range(5)]
    assert ordered in (expected, expected[::-1])


def test_analyse_road_finds_gaps() -> None:
    df = _road(4, step_deg=0.004)  # ~0.6 km between neighbours
    result = analyse_road(df, ("Avenida Teste",), max_gap_m=450)
    assert len(result.points) == 4
    assert len(result.spacings_m) == 3
    assert all(550 < d < 650 for d in result.spacings_m)
    assert len(result.gaps) == 3
    assert (
        result.class_counts()["medium (600-800 m)"]
        + result.class_counts()["reasonable (400-600 m)"]
        == 3
    )
    lat, _lon = result.gaps[0].midpoint
    assert result.points[0].lat < lat < result.points[1].lat or (
        result.points[1].lat < lat < result.points[0].lat
    )


def test_analyse_road_without_matches_raises(stops: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="no stops"):
        analyse_road(stops, ("Rua Inexistente",))
