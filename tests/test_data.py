from pathlib import Path

import pandas as pd
import pytest

from marica_transit.data import (
    GEOCODED_COLUMNS,
    Quality,
    flag_geocode_quality,
    quality_report,
    read_stops,
    write_stops,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_stops.csv"


def test_read_stops_renames_and_types() -> None:
    df = read_stops(FIXTURE)
    assert set(GEOCODED_COLUMNS) <= set(df.columns)
    assert df["stop_order"].dtype.kind == "i"
    assert df["lat"].dtype.kind == "f"


def test_read_stops_rejects_missing_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("numero_linha,nome_parada\nE01,X\n")
    with pytest.raises(ValueError, match="missing required columns"):
        read_stops(bad)


def test_write_then_read_roundtrip_keeps_portuguese_headers(tmp_path: Path) -> None:
    df = read_stops(FIXTURE)
    out = tmp_path / "nested" / "out.csv"
    write_stops(df, out, GEOCODED_COLUMNS)
    assert out.read_text(encoding="utf-8").startswith("numero_linha,nome_linha,url_linha")
    pd.testing.assert_frame_equal(read_stops(out)[GEOCODED_COLUMNS], df[GEOCODED_COLUMNS])


def test_quality_flags(stops: pd.DataFrame) -> None:
    by_name = stops.drop_duplicates("stop_name").set_index("stop_name")["geocode_quality"]
    assert by_name["Faraway"] == Quality.OUT_OF_AREA
    # three distinct names on one coordinate -> street-level fallback
    assert by_name["Rodovia Amaral Peixoto, 2"] == Quality.APPROXIMATE
    # Alpha's coordinate is shared only with itself (same name in several lines)
    assert by_name["Alpha"] == Quality.OK


def test_missing_coordinates_flagged(stops: pd.DataFrame) -> None:
    stops = stops.copy()
    stops.loc[stops["stop_name"] == "Delta", ["lat", "lon"]] = float("nan")
    flagged = flag_geocode_quality(stops)
    assert set(flagged.loc[flagged["stop_name"] == "Delta", "geocode_quality"]) == {Quality.MISSING}


def test_quality_report_counts_unique_names(stops: pd.DataFrame) -> None:
    assert quality_report(stops) == {"ok": 4, "approximate": 3, "out_of_area": 1, "missing": 0}
