"""Graph-theory metrics and routing on the bus network graph."""

from __future__ import annotations

import itertools
import logging
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

import networkx as nx

from marica_transit.geo import haversine_km

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RankedStop:
    stop: str
    value: float
    lines: int


@dataclass(slots=True)
class NetworkSummary:
    """Headline structural statistics of a (sub)network."""

    name: str
    stops: int
    edges: int
    lines: int
    density: float
    reciprocity: float
    weak_components: int
    largest_weak_component: int
    strong_components: int
    largest_strong_component: int
    total_edge_km: float
    median_edge_km: float
    approximate_edge_share: float
    suspect_edge_share: float
    median_clean_edge_km: float  # links between two precisely geocoded stops only
    # Computed on the largest strongly connected component, where every stop can reach
    # every other one by following line directions.
    scc_avg_shortest_path_km: float
    scc_avg_shortest_path_hops: float
    scc_diameter_hops: int
    top_betweenness: list[RankedStop] = field(default_factory=list)
    top_degree: list[RankedStop] = field(default_factory=list)
    top_lines_served: list[RankedStop] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ranked(graph: nx.DiGraph, scores: dict[str, float], k: int) -> list[RankedStop]:
    best = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [RankedStop(n, float(v), len(graph.nodes[n]["lines"])) for n, v in best]


def summarize(graph: nx.DiGraph, top_k: int = 10) -> NetworkSummary:
    """Compute :class:`NetworkSummary` for ``graph``."""
    if graph.number_of_nodes() == 0:
        raise ValueError("graph is empty")
    weak = list(nx.weakly_connected_components(graph))
    strong = list(nx.strongly_connected_components(graph))
    scc = graph.subgraph(max(strong, key=len))

    km_lengths = dict(nx.all_pairs_dijkstra_path_length(scc, weight="distance_km"))
    hop_lengths = dict(nx.all_pairs_shortest_path_length(scc))
    pairs = [(u, v) for u in scc for v in scc if u != v]
    n_pairs = max(len(pairs), 1)
    avg_km = sum(km_lengths[u][v] for u, v in pairs) / n_pairs
    avg_hops = sum(hop_lengths[u][v] for u, v in pairs) / n_pairs
    diameter = max((max(d.values()) for d in hop_lengths.values()), default=0)

    edge_km = [d["distance_km"] for _, _, d in graph.edges(data=True)]
    approx = sum(1 for _, _, d in graph.edges(data=True) if d["approximate"])
    suspect = sum(1 for _, _, d in graph.edges(data=True) if d["suspect"])
    clean_km = [
        d["distance_km"]
        for _, _, d in graph.edges(data=True)
        if not d["approximate"] and not d["suspect"]
    ]
    lines = {line for _, _, d in graph.edges(data=True) for line in d["lines"]}

    # Unweighted (hop-count) betweenness: it depends only on the line topology, so it is
    # robust to geocoding error, unlike distance-weighted variants.
    betweenness = nx.betweenness_centrality(graph, normalized=True)
    degree = {n: float(d) for n, d in graph.degree()}
    served = {n: float(len(d["lines"])) for n, d in graph.nodes(data=True)}

    return NetworkSummary(
        name=graph.graph.get("name", "network"),
        stops=graph.number_of_nodes(),
        edges=graph.number_of_edges(),
        lines=len(lines),
        density=nx.density(graph),
        reciprocity=nx.overall_reciprocity(graph) if graph.number_of_edges() else 0.0,
        weak_components=len(weak),
        largest_weak_component=max(map(len, weak)),
        strong_components=len(strong),
        largest_strong_component=scc.number_of_nodes(),
        total_edge_km=sum(edge_km),
        median_edge_km=statistics.median(edge_km) if edge_km else 0.0,
        approximate_edge_share=approx / len(edge_km) if edge_km else 0.0,
        suspect_edge_share=suspect / len(edge_km) if edge_km else 0.0,
        median_clean_edge_km=statistics.median(clean_km) if clean_km else 0.0,
        scc_avg_shortest_path_km=avg_km,
        scc_avg_shortest_path_hops=avg_hops,
        scc_diameter_hops=diameter,
        top_betweenness=_ranked(graph, betweenness, top_k),
        top_degree=_ranked(graph, degree, top_k),
        top_lines_served=_ranked(graph, served, top_k),
    )


def format_summary(s: NetworkSummary) -> str:
    """Human-readable report of a :class:`NetworkSummary`."""
    rows = [
        f"Network: {s.name}",
        f"  stops (nodes)                 {s.stops}",
        f"  stop-to-stop links (edges)    {s.edges}",
        f"  bus lines                     {s.lines}",
        f"  density                       {s.density:.5f}",
        f"  reciprocity                   {s.reciprocity:.3f}",
        f"  weakly connected components   {s.weak_components}"
        f" (largest: {s.largest_weak_component})",
        f"  strongly connected components {s.strong_components}"
        f" (largest: {s.largest_strong_component})",
        "  largest SCC (topology only):",
        f"    avg shortest path           {s.scc_avg_shortest_path_hops:.1f} stops",
        f"    diameter                    {s.scc_diameter_hops} stops",
        "  distances (straight line between geocoded stops; see caveats):",
        f"    median link                 {s.median_edge_km * 1000:.0f} m"
        f" (precise geocodes only: {s.median_clean_edge_km * 1000:.0f} m)",
        f"    links with approx. endpoint {s.approximate_edge_share:.1%}",
        f"    links > 5 km (suspect)      {s.suspect_edge_share:.1%}",
        f"    total link length           {s.total_edge_km:.0f} km",
        f"    avg shortest path (SCC)     {s.scc_avg_shortest_path_km:.1f} km",
    ]
    for title, ranking, fmt in (
        ("Top stops by betweenness centrality", s.top_betweenness, "{:.3f}"),
        ("Top stops by degree (in + out links)", s.top_degree, "{:.0f}"),
        ("Top stops by number of lines served", s.top_lines_served, "{:.0f}"),
    ):
        rows.append(f"{title}:")
        rows.extend(
            f"  {i:>2}. {r.stop:<55} {fmt.format(r.value):>7}  ({r.lines} lines)"
            for i, r in enumerate(ranking, 1)
        )
    return "\n".join(rows)


# --- Routing -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Route:
    """A shortest path and the line-level interpretation of it."""

    stops: list[str]
    distance_km: float
    # Lines usable on each leg (between stops[i] and stops[i + 1]).
    leg_lines: list[list[str]]
    # Fewest line changes needed to ride this exact path, with the line used per segment.
    rides: list[tuple[str, int]]

    @property
    def transfers(self) -> int:
        return max(len(self.rides) - 1, 0)


def nearest_stop(graph: nx.DiGraph, lat: float, lon: float) -> tuple[str, float]:
    """Return the stop closest to (lat, lon) and its distance in km."""
    if graph.number_of_nodes() == 0:
        raise ValueError("graph is empty")
    best = min(
        graph.nodes(data=True), key=lambda nd: haversine_km(lat, lon, nd[1]["lat"], nd[1]["lon"])
    )
    return best[0], haversine_km(lat, lon, best[1]["lat"], best[1]["lon"])


def stops_near(
    graph: nx.DiGraph, lat: float, lon: float, tolerance_km: float = 0.005
) -> tuple[list[str], float]:
    """All stops tied for nearest to (lat, lon), within ``tolerance_km`` of the best.

    Several stop names often share one (approximate) coordinate, so a coordinate does not
    identify a single stop; routing should consider every co-located candidate.
    """
    _, best = nearest_stop(graph, lat, lon)
    near = [
        n
        for n, d in graph.nodes(data=True)
        if haversine_km(lat, lon, d["lat"], d["lon"]) <= best + tolerance_km
    ]
    return sorted(near), best


def plan_rides(leg_lines: list[list[str]]) -> list[tuple[str, int]]:
    """Fewest-transfer assignment of lines to consecutive legs.

    Greedily stay on the set of lines that serve every leg so far; switching only when the
    set becomes empty is optimal for a fixed path. Returns ``(line, n_legs)`` per ride.
    """
    rides: list[tuple[str, int]] = []
    current: set[str] = set()
    count = 0
    for options in leg_lines:
        narrowed = current & set(options)
        if narrowed:
            current, count = narrowed, count + 1
            continue
        if current:
            rides.append((min(current), count))
        current, count = set(options), 1
    if current:
        rides.append((min(current), count))
    return rides


def _route_from_path(graph: nx.DiGraph, path: list[str]) -> Route:
    legs = list(itertools.pairwise(path))
    leg_lines = [list(graph.edges[u, v]["lines"]) for u, v in legs]
    km = sum(graph.edges[u, v]["distance_km"] for u, v in legs)
    return Route(stops=path, distance_km=km, leg_lines=leg_lines, rides=plan_rides(leg_lines))


def shortest_route(graph: nx.DiGraph, source: str, target: str) -> Route:
    """Distance-weighted shortest path (Dijkstra). Raises ``nx.NetworkXNoPath``."""
    path = nx.dijkstra_path(graph, source, target, weight="distance_km")
    return _route_from_path(graph, path)


def best_route(graph: nx.DiGraph, sources: list[str], targets: list[str]) -> Route:
    """Shortest route from any of ``sources`` to any of ``targets`` (Dijkstra)."""
    lengths, paths = nx.multi_source_dijkstra(graph, set(sources), weight="distance_km")
    reachable = [t for t in targets if t in lengths]
    if not reachable:
        raise nx.NetworkXNoPath(f"no path from {sources} to {targets}")
    target = min(reachable, key=lambda t: (lengths[t], len(paths[t]), t))
    return _route_from_path(graph, paths[target])


def shortest_route_astar(graph: nx.DiGraph, source: str, target: str) -> Route:
    """A* with the straight-line (haversine) distance as an admissible heuristic."""

    def heuristic(u: str, v: str) -> float:
        a, b = graph.nodes[u], graph.nodes[v]
        return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])

    path = nx.astar_path(graph, source, target, heuristic=heuristic, weight="distance_km")
    return _route_from_path(graph, path)
