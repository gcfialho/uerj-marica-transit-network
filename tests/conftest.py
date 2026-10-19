from pathlib import Path

import networkx as nx
import pandas as pd
import pytest

from marica_transit.data import flag_geocode_quality, read_stops
from marica_transit.graph import build_graph

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_stops.csv"


@pytest.fixture
def stops() -> pd.DataFrame:
    return flag_geocode_quality(read_stops(FIXTURE))


@pytest.fixture
def graph(stops: pd.DataFrame) -> nx.DiGraph:
    return build_graph(stops)
