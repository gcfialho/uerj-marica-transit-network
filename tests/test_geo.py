import math

import pytest

from marica_transit.geo import EARTH_RADIUS_KM, ITAIPUACU, MARICA, BoundingBox, haversine_km


def test_haversine_zero_distance() -> None:
    assert haversine_km(-22.96, -42.98, -22.96, -42.98) == 0.0


def test_haversine_one_degree_of_latitude() -> None:
    expected = math.pi * EARTH_RADIUS_KM / 180  # ~111.195 km
    assert haversine_km(-23.0, -42.9, -22.0, -42.9) == pytest.approx(expected, rel=1e-9)


def test_haversine_longitude_shrinks_with_latitude() -> None:
    equator = haversine_km(0.0, 0.0, 0.0, 1.0)
    marica = haversine_km(-22.95, -42.9, -22.95, -41.9)
    assert marica == pytest.approx(equator * math.cos(math.radians(22.95)), rel=1e-3)


def test_haversine_is_symmetric_and_matches_known_value() -> None:
    # Rio de Janeiro (Centro) -> Maricá (Centro); ~36 km in a straight line.
    d1 = haversine_km(-22.9068, -43.1729, -22.9194, -42.8186)
    d2 = haversine_km(-22.9194, -42.8186, -22.9068, -43.1729)
    assert d1 == d2
    assert 35 < d1 < 37


def test_haversine_rejects_nan() -> None:
    with pytest.raises(ValueError, match="NaN"):
        haversine_km(float("nan"), 0, 0, 0)


def test_bbox_contains_is_inclusive() -> None:
    box = BoundingBox("t", -1, 1, -2, 2)
    assert box.contains(1, 2)
    assert box.contains(0, 0)
    assert not box.contains(1.0001, 0)


def test_bbox_validates_bounds() -> None:
    with pytest.raises(ValueError, match="invalid"):
        BoundingBox("bad", 1, -1, 0, 1)


def test_itaipuacu_is_inside_marica() -> None:
    assert MARICA.contains(ITAIPUACU.lat_min, ITAIPUACU.lon_min)
    assert MARICA.contains(ITAIPUACU.lat_max, ITAIPUACU.lon_max)
    assert not MARICA.contains(-20.0170955, -45.9795138)  # Bambuí (MG), a real geocoding error
