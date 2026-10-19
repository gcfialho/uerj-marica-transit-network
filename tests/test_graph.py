from pathlib import Path

import networkx as nx
import pandas as pd
import pytest

from marica_transit.geo import ITAIPUACU, BoundingBox, haversine_km
from marica_transit.graph import build_graph, subgraph_in_bbox, write_graphml


def test_nodes_are_unique_stop_names_without_bad_geocodes(graph: nx.DiGraph) -> None:
    assert set(graph.nodes) == {
        "Alpha",
        "Beta",
        "Gamma",
        "Delta",
        "Rodovia Amaral Peixoto, 1",
        "Rodovia Amaral Peixoto, 2",
        "Rodovia Amaral Peixoto, 3",
    }
    assert "Faraway" not in graph


def test_edges_follow_stop_order(graph: nx.DiGraph) -> None:
    assert set(graph.edges) == {
        ("Alpha", "Beta"),
        ("Beta", "Gamma"),  # bridged over the out-of-area stop
        ("Gamma", "Delta"),
        ("Delta", "Alpha"),
        ("Rodovia Amaral Peixoto, 1", "Rodovia Amaral Peixoto, 2"),
        ("Rodovia Amaral Peixoto, 2", "Rodovia Amaral Peixoto, 3"),
    }


def test_consecutive_duplicates_do_not_create_self_loops(graph: nx.DiGraph) -> None:
    assert nx.number_of_selfloops(graph) == 0


def test_shared_edge_records_every_line(graph: nx.DiGraph) -> None:
    # Regression: the original code only appended a line when the new distance was
    # strictly shorter, so a link shared by several lines listed just one of them.
    assert graph.edges["Alpha", "Beta"]["lines"] == ["L1", "L3"]
    assert graph.nodes["Alpha"]["lines"] == ["L1", "L2", "L3"]


def test_edge_distance_is_haversine(graph: nx.DiGraph) -> None:
    expected = haversine_km(-22.96, -42.98, -22.96, -42.97)
    assert graph.edges["Alpha", "Beta"]["distance_km"] == pytest.approx(expected)
    assert graph.edges["Alpha", "Beta"]["distance_km"] == pytest.approx(1.024, abs=1e-3)


def test_approximate_edges_flagged(graph: nx.DiGraph) -> None:
    assert graph.edges["Rodovia Amaral Peixoto, 1", "Rodovia Amaral Peixoto, 2"]["approximate"]
    assert not graph.edges["Alpha", "Beta"]["approximate"]


def test_unsorted_input_gives_same_graph(stops: pd.DataFrame) -> None:
    shuffled = stops.sample(frac=1.0, random_state=0)
    assert set(build_graph(shuffled).edges) == set(build_graph(stops).edges)


def test_subgraph_in_bbox_is_induced(graph: nx.DiGraph) -> None:
    sub = subgraph_in_bbox(graph, ITAIPUACU)
    assert set(sub.nodes) == {"Alpha", "Beta", "Gamma", "Delta"}
    assert sub.number_of_edges() == 4


def test_subgraph_does_not_join_stops_across_the_boundary(graph: nx.DiGraph) -> None:
    # Box that excludes Delta: Gamma and Alpha are both inside but only linked via Delta.
    box = BoundingBox("west-south", -22.99, -22.955, -43.0, -42.95)
    sub = subgraph_in_bbox(graph, box)
    assert "Delta" not in sub
    assert not sub.has_edge("Gamma", "Alpha")


def test_graphml_roundtrip(graph: nx.DiGraph, tmp_path: Path) -> None:
    path = tmp_path / "g.graphml"
    write_graphml(graph, path)
    loaded = nx.read_graphml(path)
    assert loaded.number_of_nodes() == graph.number_of_nodes()
    assert loaded.number_of_edges() == graph.number_of_edges()
    assert loaded.edges["Alpha", "Beta"]["lines"] == "L1,L3"


def test_long_links_flagged_as_suspect(stops: pd.DataFrame) -> None:
    moved = stops.copy()
    # Delta "geocoded" to a homonymous street ~28 km east, still inside Maricá.
    moved.loc[moved["stop_name"] == "Delta", ["lat", "lon"]] = (-22.93, -42.70)
    g = build_graph(moved)
    assert g.edges["Gamma", "Delta"]["suspect"]
    assert g.edges["Delta", "Alpha"]["suspect"]
    assert not g.edges["Alpha", "Beta"]["suspect"]
