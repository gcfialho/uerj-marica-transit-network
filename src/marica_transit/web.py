"""Data bundle for the interactive explorer published from ``docs/``.

Everything the browser needs is computed here, once, by the same code that produces the
README numbers: the graph, centrality, both regional summaries, the route case study and
the Av. Carlos Marighella stop-spacing study. The page itself only draws and filters.
"""

from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

from marica_transit import analysis, coverage
from marica_transit.config import DEFAULT_DESTINATION, DEFAULT_ORIGIN
from marica_transit.data import quality_report
from marica_transit.geo import ITAIPUACU, MARICA, BoundingBox
from marica_transit.graph import EXCLUDED_QUALITIES, StopGraph, subgraph_in_bbox

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _box(box: BoundingBox) -> dict[str, Any]:
    return {
        "name": box.name,
        "label": box.display_name,
        "bounds": [[box.lon_min, box.lat_min], [box.lon_max, box.lat_max]],
    }


def _line_sequences(stops: pd.DataFrame, ids: dict[str, int]) -> list[dict[str, Any]]:
    """Ordered stop ids per line and direction, following the graph's rules.

    Unusable geocodes are bridged over and repeated consecutive stops are skipped, exactly
    as in :func:`marica_transit.graph.build_graph`, so a drawn line matches its edges.
    """
    usable = stops[~stops["geocode_quality"].isin(EXCLUDED_QUALITIES)]
    ordered = usable.sort_values(["line_code", "direction", "stop_order"])
    out = []
    for (code, direction), seq in ordered.groupby(["line_code", "direction"], sort=True):
        sequence: list[int] = []
        for name in seq["stop_name"]:
            sid = ids[name]
            if not sequence or sequence[-1] != sid:
                sequence.append(sid)
        first = seq.iloc[0]
        out.append(
            {
                "code": str(code),
                "name": str(first["line_name"]),
                "direction": str(direction),
                "url": str(first["line_url"]) if pd.notna(first["line_url"]) else "",
                "stops": sequence,
            }
        )
    return out


def _route_case(graph: StopGraph, ids: dict[str, int]) -> dict[str, Any] | None:
    net = subgraph_in_bbox(graph, ITAIPUACU)
    sources, _ = analysis.stops_near(net, *DEFAULT_ORIGIN)
    targets, _ = analysis.stops_near(net, *DEFAULT_DESTINATION)
    try:
        route = analysis.best_route(net, sources, targets)
    except nx.NetworkXNoPath:
        return None
    astar = analysis.shortest_route_astar(net, route.stops[0], route.stops[-1])
    return {
        "origin": list(DEFAULT_ORIGIN),
        "destination": list(DEFAULT_DESTINATION),
        "stops": [ids[s] for s in route.stops],
        "km": round(route.distance_km, 3),
        "rides": [[line, n] for line, n in route.rides],
        "astar_km": round(astar.distance_km, 3),
    }


# Corridors shown in the explorer. Only roads with enough precisely geocoded stops to give
# a meaningful spacing profile are included (see the README for why others were left out).
CORRIDORS: tuple[dict[str, Any], ...] = (
    {
        "key": "marighella",
        "label": "Avenida Carlos Marighella",
        "short": "Av. Carlos Marighella",
        "streets": coverage.CARLOS_MARIGHELLA,
        "region": ITAIPUACU,
    },
    {
        "key": "rj106",
        "label": "Rodovia Amaral Peixoto (RJ-106)",
        "short": "RJ-106",
        "streets": ("Amaral Peixoto", "RJ 106", "RJ-106"),
        "region": None,
    },
)

# Hops longer than this between consecutive matched stops are holes in the geocoded data
# (stops collapsed onto one approximate point), not real spacing. The corridor keeps its
# longest stretch without such a hole.
DATA_GAP_M = 3000.0


def _corridor(stops: pd.DataFrame, spec: dict[str, Any]) -> dict[str, Any]:
    box: BoundingBox | None = spec["region"]
    if box is not None:
        stops = stops[[box.contains(a, b) for a, b in zip(stops["lat"], stops["lon"], strict=True)]]
    result = coverage.analyse_road(stops, spec["streets"])
    points, spacings = result.points, result.spacings_m

    # Longest run of consecutive points with no data gap.
    best, run_start = (0, 0), 0
    for i in range(len(spacings) + 1):
        if i == len(spacings) or spacings[i] > DATA_GAP_M:
            if i - run_start > best[1] - best[0]:
                best = (run_start, i)
            run_start = i + 1
    lo, hi = best
    kept_points = points[lo : hi + 1]
    kept_spacings = spacings[lo:hi]
    return {
        "key": spec["key"],
        "street": spec["label"],
        "short": spec["short"],
        "region": box.name if box is not None else "marica",
        "default_max_gap_m": result.max_gap_m,
        "dropped_points": len(points) - len(kept_points),
        "points": [
            {
                "lat": round(p.lat, 6),
                "lon": round(p.lon, 6),
                "stops": list(p.stop_names),
                "lines": list(p.lines),
                "approximate": p.approximate,
            }
            for p in kept_points
        ],
        "spacings_m": [round(d, 1) for d in kept_spacings],
        "classes": [
            [None if upper == float("inf") else upper, colour]
            for upper, _, colour in coverage.SPACING_CLASSES
        ],
    }


def _corridors(stops: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for spec in CORRIDORS:
        try:
            out.append(_corridor(stops, spec))
        except ValueError:  # the road is not in this dataset (e.g. a test fixture)
            log.info("corridor %s: no matching stops, skipped", spec["key"])
    return out


def build_bundle(graph: StopGraph, stops: pd.DataFrame) -> dict[str, Any]:
    """Return the JSON-serialisable bundle consumed by ``docs/app.js``."""
    names = sorted(graph.nodes)
    ids = {name: i for i, name in enumerate(names)}

    ita = subgraph_in_bbox(graph, ITAIPUACU)
    bt = nx.betweenness_centrality(graph, normalized=True)
    bt_ita = nx.betweenness_centrality(ita, normalized=True)

    stop_rows = []
    for name in names:
        d = graph.nodes[name]
        row: dict[str, Any] = {
            "id": ids[name],
            "name": name,
            "lat": round(d["lat"], 6),
            "lon": round(d["lon"], 6),
            "address": d["address"],
            "quality": d["quality"],
            "lines": d["lines"],
            "in": graph.in_degree(name),
            "out": graph.out_degree(name),
            "bt": round(bt[name], 5),
        }
        if name in ita:
            row["bt_ita"] = round(bt_ita[name], 5)
        stop_rows.append(row)

    edge_rows = [
        {
            "s": ids[u],
            "t": ids[v],
            "km": round(d["distance_km"], 3),
            "lines": d["lines"],
            "approximate": d["approximate"],
            "suspect": d["suspect"],
        }
        for u, v, d in graph.edges(data=True)
    ]

    def summary(g: StopGraph) -> dict[str, Any]:
        s = analysis.summarize(g, top_k=10).to_dict()
        for key in ("top_betweenness", "top_degree", "top_lines_served"):
            s[key] = [{**r, "id": ids[r["stop"]]} for r in s[key]]
        return s

    bundle: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "source": "Moovit snapshot (2025), geocoded with Nominatim and Google",
        "regions": {box.name: _box(box) for box in (MARICA, ITAIPUACU)},
        "geocode_quality": quality_report(stops),
        "summaries": {"marica": summary(graph), "itaipuacu": summary(ita)},
        "stops": stop_rows,
        "edges": edge_rows,
        "lines": _line_sequences(stops, ids),
        "route_case": _route_case(graph, ids),
        "corridors": _corridors(stops),
    }
    # Sanity: every consecutive pair in a line sequence must be an edge of the graph.
    for line in bundle["lines"]:
        for a, b in itertools.pairwise(line["stops"]):
            if not graph.has_edge(names[a], names[b]):
                raise AssertionError(f"line {line['code']} hop {a}->{b} is not a graph edge")
    return bundle


def write_bundle(graph: StopGraph, stops: pd.DataFrame, path: Path) -> dict[str, Any]:
    """Build the bundle and write it as compact UTF-8 JSON."""
    bundle = build_bundle(graph, stops)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + "\n")
    log.info(
        "wrote explorer data (%d stops, %d links) to %s",
        len(bundle["stops"]),
        len(bundle["edges"]),
        path,
    )
    return bundle
