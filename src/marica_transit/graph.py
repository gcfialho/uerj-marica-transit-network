"""Directed graph model of the bus network.

* **Node** = a unique Moovit stop name, positioned at its geocoded coordinate.
* **Edge** ``u -> v`` = some line serves ``v`` immediately after ``u`` in its stop sequence.
  Edges carry the great-circle length and the set of lines that use them.

Nodes are keyed by stop name rather than by coordinate on purpose: when the geocoder falls
back to a street centroid, dozens of different stops share one coordinate, and keying by
coordinate would merge them into a single artificial "hub".
"""

from __future__ import annotations

import itertools
import logging
from pathlib import Path

import networkx as nx
import pandas as pd

from marica_transit.data import Quality
from marica_transit.geo import BoundingBox, haversine_km

log = logging.getLogger(__name__)

# Stops with these quality flags are left out of the graph. A line's sequence is bridged
# over them, so the route stays connected (the bus still goes from the previous stop to
# the next one), instead of being split as the original implementation did.
EXCLUDED_QUALITIES = frozenset({Quality.OUT_OF_AREA, Quality.MISSING})

# Consecutive stops of a bus line more than this far apart (straight line) almost always
# mean that one of them was geocoded to a homonymous street elsewhere in town
# (e.g. "Rua Nove, 58" placed 33 km from its neighbours). Such links are kept, since the
# topology is right, but flagged so distance-based results can be read with care.
SUSPECT_LINK_KM = 5.0


def build_graph(stops: pd.DataFrame) -> nx.DiGraph:
    """Build the network graph from a quality-flagged stops DataFrame.

    Required columns: ``line_code, direction, stop_order, stop_name, lat, lon,
    geocoded_address, geocode_quality``.
    """
    usable = stops[~stops["geocode_quality"].isin(EXCLUDED_QUALITIES)]
    dropped = stops.loc[stops["geocode_quality"].isin(EXCLUDED_QUALITIES), "stop_name"]
    if not dropped.empty:
        log.info(
            "excluding %d stop(s) with unusable geocodes: %s",
            dropped.nunique(),
            ", ".join(sorted(dropped.unique())),
        )

    graph = nx.DiGraph(name="Maricá EPT bus network")
    lines_at_stop = usable.groupby("stop_name")["line_code"].agg(lambda s: sorted(set(s)))
    for row in usable.drop_duplicates("stop_name").itertuples(index=False):
        graph.add_node(
            row.stop_name,
            lat=float(row.lat),
            lon=float(row.lon),
            address=str(row.geocoded_address) if pd.notna(row.geocoded_address) else "",
            quality=row.geocode_quality,
            lines=lines_at_stop[row.stop_name],
        )

    ordered = usable.sort_values(["line_code", "direction", "stop_order"])
    for (line, _direction), seq in ordered.groupby(["line_code", "direction"], sort=False):
        names = seq["stop_name"].tolist()
        for u, v in itertools.pairwise(names):
            if u == v:  # consecutive duplicate entries in Moovit; not a real hop
                continue
            if graph.has_edge(u, v):
                edge_lines = graph.edges[u, v]["lines"]
                if line not in edge_lines:
                    edge_lines.append(line)
                    edge_lines.sort()
                continue
            nu, nv = graph.nodes[u], graph.nodes[v]
            km = haversine_km(nu["lat"], nu["lon"], nv["lat"], nv["lon"])
            graph.add_edge(
                u,
                v,
                distance_km=km,
                lines=[line],
                approximate=Quality.APPROXIMATE in (nu["quality"], nv["quality"]),
                suspect=km > SUSPECT_LINK_KM,
            )
    log.info(
        "built graph: %d stops, %d directed edges",
        graph.number_of_nodes(),
        graph.number_of_edges(),
    )
    return graph


def subgraph_in_bbox(graph: nx.DiGraph, bbox: BoundingBox) -> nx.DiGraph:
    """Induced subgraph of the stops inside ``bbox``.

    Only edges whose *both* endpoints are inside are kept; consecutive in-box stops of a
    line that leaves and re-enters the box are deliberately **not** joined.
    """
    nodes = [n for n, d in graph.nodes(data=True) if bbox.contains(d["lat"], d["lon"])]
    sub = graph.subgraph(nodes).copy()
    sub.graph["name"] = f"{graph.graph.get('name', 'network')}, {bbox.display_name} subgraph"
    return sub


def write_graphml(graph: nx.DiGraph, path: Path) -> None:
    """Export to GraphML (list attributes are flattened to comma-separated strings).

    GraphML is a portable, non-executable alternative to the pickle cache that the
    original project committed (unpickling an untrusted file can run arbitrary code).
    """
    flat = nx.DiGraph(name=graph.graph.get("name", ""))
    for n, d in graph.nodes(data=True):
        flat.add_node(n, **{k: ",".join(v) if isinstance(v, list) else v for k, v in d.items()})
    for u, v, d in graph.edges(data=True):
        flat.add_edge(u, v, **{k: ",".join(x) if isinstance(x, list) else x for k, x in d.items()})
    path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(flat, path)
    log.info("wrote GraphML to %s", path)
