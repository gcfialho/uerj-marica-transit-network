"""Data acquisition: committed snapshot by default, network only when forced."""

from __future__ import annotations

import logging

import networkx as nx
import pandas as pd

from marica_transit.config import EPT_LINES_URL, Paths
from marica_transit.data import (
    GEOCODED_COLUMNS,
    RAW_COLUMNS,
    flag_geocode_quality,
    read_stops,
    write_stops,
)
from marica_transit.geocoder import Geocoder, Service
from marica_transit.graph import build_graph
from marica_transit.scraper import MoovitScraper

log = logging.getLogger(__name__)


def load_stops(
    paths: Paths,
    force_rescrape: bool = False,
    force_regeocode: bool = False,
    geocoder_service: Service = "both",
    contact: str | None = None,
) -> pd.DataFrame:
    """Return quality-flagged, geocoded stop sequences.

    * default: read ``data/processed/moovit_stops_geocoded.csv`` (offline);
    * ``force_regeocode``: geocode the raw snapshot again (network);
    * ``force_rescrape``: scrape Moovit again, then geocode (network).
    """
    if force_rescrape or force_regeocode:
        if force_rescrape:
            raw = MoovitScraper().scrape(EPT_LINES_URL)
            write_stops(raw, paths.raw_stops, RAW_COLUMNS)
        else:
            raw = read_stops(paths.raw_stops)
        geocoded = Geocoder(contact=contact).geocode_stops(raw, service=geocoder_service)
        write_stops(geocoded, paths.geocoded_stops, [*GEOCODED_COLUMNS, "geocoding_source"])
    if not paths.geocoded_stops.exists():
        raise FileNotFoundError(
            f"{paths.geocoded_stops} not found; run with --force-regeocode or restore the snapshot"
        )
    stops = read_stops(paths.geocoded_stops)
    log.info("loaded %d stop rows from %s", len(stops), paths.geocoded_stops)
    return flag_geocode_quality(stops)


def load_graph(
    paths: Paths,
    force_rescrape: bool = False,
    force_regeocode: bool = False,
    geocoder_service: Service = "both",
    contact: str | None = None,
) -> tuple[pd.DataFrame, nx.DiGraph]:
    """Load stops (see :func:`load_stops`) and build the network graph.

    The graph is rebuilt from CSV on every run (well under a second), replacing the
    original pickle cache, which was both an arbitrary-code-execution risk when loaded
    from a cloned repo and invalidated by unreliable file modification times.
    """
    stops = load_stops(paths, force_rescrape, force_regeocode, geocoder_service, contact)
    return stops, build_graph(stops)
