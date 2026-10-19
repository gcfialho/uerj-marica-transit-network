import networkx as nx
import pytest

from marica_transit.analysis import (
    format_summary,
    nearest_stop,
    plan_rides,
    shortest_route,
    shortest_route_astar,
    summarize,
)


def test_summary_structure(graph: nx.DiGraph) -> None:
    s = summarize(graph, top_k=3)
    assert (s.stops, s.edges, s.lines) == (7, 6, 4)
    assert s.weak_components == 2
    assert s.largest_weak_component == 4
    assert s.strong_components == 4  # the 4-cycle + three singleton stops
    assert s.largest_strong_component == 4
    assert s.density == pytest.approx(6 / (7 * 6))
    assert s.reciprocity == 0.0
    assert s.approximate_edge_share == pytest.approx(2 / 6)
    assert len(s.top_betweenness) == 3


def test_summary_shortest_paths_in_largest_scc(graph: nx.DiGraph) -> None:
    s = summarize(graph)
    # In a directed 4-cycle every ordered pair is 1, 2 or 3 hops apart: mean 2, diameter 3.
    assert s.scc_avg_shortest_path_hops == pytest.approx(2.0)
    assert s.scc_diameter_hops == 3
    assert s.scc_avg_shortest_path_km > 0


def test_cycle_nodes_have_highest_betweenness(graph: nx.DiGraph) -> None:
    s = summarize(graph, top_k=4)
    assert {r.stop for r in s.top_betweenness} == {"Alpha", "Beta", "Gamma", "Delta"}
    assert "Alpha" in format_summary(s)


def test_summarize_rejects_empty_graph() -> None:
    with pytest.raises(ValueError, match="empty"):
        summarize(nx.DiGraph())


def test_dijkstra_and_astar_agree(graph: nx.DiGraph) -> None:
    route = shortest_route(graph, "Beta", "Alpha")
    astar = shortest_route_astar(graph, "Beta", "Alpha")
    assert route.stops == ["Beta", "Gamma", "Delta", "Alpha"]
    assert astar.stops == route.stops
    assert astar.distance_km == pytest.approx(route.distance_km)
    expected = sum(graph.edges[u, v]["distance_km"] for u, v in nx.utils.pairwise(route.stops))
    assert route.distance_km == pytest.approx(expected)


def test_route_respects_direction(graph: nx.DiGraph) -> None:
    with pytest.raises(nx.NetworkXNoPath):
        shortest_route(graph, "Rodovia Amaral Peixoto, 3", "Rodovia Amaral Peixoto, 1")


def test_route_ride_plan(graph: nx.DiGraph) -> None:
    route = shortest_route(graph, "Beta", "Alpha")
    # Beta->Gamma is L1 only, Gamma->Delta->Alpha is L2: one transfer at Gamma.
    assert route.rides == [("L1", 1), ("L2", 2)]
    assert route.transfers == 1


def test_plan_rides_minimises_transfers() -> None:
    legs = [["A", "B"], ["B", "C"], ["C"], ["C", "D"], ["D"]]
    assert plan_rides(legs) == [("B", 2), ("C", 2), ("D", 1)]
    assert plan_rides([]) == []


def test_nearest_stop(graph: nx.DiGraph) -> None:
    stop, dist = nearest_stop(graph, -22.9601, -42.9701)
    assert stop == "Beta"
    assert dist < 0.02
