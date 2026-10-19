"""Loading, validating and quality-flagging the stop-sequence CSVs.

The committed CSVs keep the original Portuguese headers of the 2025 snapshot; they are
renamed to English on load and back on save, so the data files stay byte-compatible.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

import pandas as pd

from marica_transit.geo import MARICA, BoundingBox

log = logging.getLogger(__name__)

# CSV header (Portuguese, as committed) -> in-memory column name.
COLUMNS: Final[dict[str, str]] = {
    "numero_linha": "line_code",
    "nome_linha": "line_name",
    "url_linha": "line_url",
    "sentido": "direction",
    "ordem_parada": "stop_order",
    "nome_parada": "stop_name",
    "latitude": "lat",
    "longitude": "lon",
    "endereco_geocodificado": "geocoded_address",
    "geocoding_source": "geocoding_source",
}
RAW_COLUMNS: Final = ["line_code", "line_name", "line_url", "direction", "stop_order", "stop_name"]
GEOCODED_COLUMNS: Final = [*RAW_COLUMNS, "lat", "lon", "geocoded_address"]

# A coordinate shared by this many distinct stop names is almost certainly a street- or
# neighbourhood-level fallback (e.g. 33 different "Rodovia Amaral Peixoto, <n>" stops all
# resolve to one point), not the stop's real position.
APPROXIMATE_MIN_SHARED_NAMES: Final = 3


class Quality:
    OK = "ok"
    APPROXIMATE = "approximate"
    OUT_OF_AREA = "out_of_area"
    MISSING = "missing"


def read_stops(path: Path) -> pd.DataFrame:
    """Read a raw or geocoded stops CSV and normalise column names and dtypes."""
    df = pd.read_csv(path, dtype={"numero_linha": str, "sentido": str, "nome_parada": str})
    df = df.rename(columns=COLUMNS)
    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")
    df["stop_order"] = pd.to_numeric(df["stop_order"], errors="raise").astype(int)
    for col in ("lat", "lon"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    log.debug("loaded %d rows from %s", len(df), path)
    return df


def write_stops(df: pd.DataFrame, path: Path, columns: list[str]) -> None:
    """Write ``columns`` of ``df`` using the original Portuguese CSV headers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    inverse = {v: k for k, v in COLUMNS.items()}
    out = df.reindex(columns=columns).rename(columns=inverse)
    out.to_csv(path, index=False, encoding="utf-8")
    log.info("wrote %d rows to %s", len(out), path)


def flag_geocode_quality(df: pd.DataFrame, area: BoundingBox = MARICA) -> pd.DataFrame:
    """Return a copy of ``df`` with a ``geocode_quality`` column (see :class:`Quality`)."""
    out = df.copy()
    has_coords = out["lat"].notna() & out["lon"].notna()
    in_area = has_coords & pd.Series(
        [area.contains(lat, lon) for lat, lon in zip(out["lat"], out["lon"], strict=True)],
        index=out.index,
    )
    names_per_coord = (
        out[has_coords].groupby(["lat", "lon"])["stop_name"].nunique().rename("names_per_coord")
    )
    out = out.join(names_per_coord, on=["lat", "lon"])
    shared = out["names_per_coord"].fillna(0) >= APPROXIMATE_MIN_SHARED_NAMES

    quality = pd.Series(Quality.OK, index=out.index)
    quality[shared] = Quality.APPROXIMATE
    quality[has_coords & ~in_area] = Quality.OUT_OF_AREA
    quality[~has_coords] = Quality.MISSING
    out["geocode_quality"] = quality
    return out.drop(columns="names_per_coord")


def quality_report(df: pd.DataFrame) -> dict[str, int]:
    """Count unique stop names per geocode-quality class."""
    stops = df.drop_duplicates("stop_name")
    counts = stops["geocode_quality"].value_counts()
    return {
        q: int(counts.get(q, 0))
        for q in (Quality.OK, Quality.APPROXIMATE, Quality.OUT_OF_AREA, Quality.MISSING)
    }
