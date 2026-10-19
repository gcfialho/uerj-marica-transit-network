"""Command-line interface: ``uv run marica-transit <command> [options]``."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path

import networkx as nx
import pandas as pd

from marica_transit import analysis, coverage, maps
from marica_transit.config import Paths
from marica_transit.data import quality_report
from marica_transit.geo import REGIONS
from marica_transit.graph import subgraph_in_bbox, write_graphml
from marica_transit.pipeline import load_graph

log = logging.getLogger("marica_transit")

# Origin/destination of the course project's route case study: the eastern and western
# ends of Jardim Atlântico, Itaipuaçu. The nearest graph stop to each point is used.
DEFAULT_ORIGIN = (-22.9672379, -42.9099213)
DEFAULT_DESTINATION = (-22.9675662, -42.9707889)


def _coord(text: str) -> tuple[float, float]:
    try:
        lat, lon = (float(x) for x in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected 'lat,lon', got {text!r}") from exc
    return lat, lon


def _load(args: argparse.Namespace) -> tuple[Paths, nx.DiGraph, pd.DataFrame]:
    paths = Paths(data_dir=args.data_dir)
    stops, graph = load_graph(
        paths,
        force_rescrape=args.force_rescrape,
        force_regeocode=args.force_regeocode,
        geocoder_service=args.geocoder,
        contact=args.contact,
    )
    log.info("geocode quality (unique stop names): %s", quality_report(stops))
    return paths, graph, stops


def _region_graph(graph: nx.DiGraph, region: str) -> nx.DiGraph:
    return graph if region == "marica" else subgraph_in_bbox(graph, REGIONS[region])


def _hubs(summary: analysis.NetworkSummary) -> list[tuple[str, float]]:
    return [(r.stop, r.value) for r in summary.top_betweenness]


# --- commands ------------------------------------------------------------------------


def cmd_build_graph(args: argparse.Namespace) -> None:
    _, graph, _ = _load(args)
    write_graphml(graph, args.graphml)
    print(f"{graph.number_of_nodes()} stops, {graph.number_of_edges()} links -> {args.graphml}")


def cmd_analyze(args: argparse.Namespace) -> None:
    _, graph, stops = _load(args)
    report = {"geocode_quality": quality_report(stops), "networks": []}
    for region in args.region:
        summary = analysis.summarize(_region_graph(graph, region), top_k=args.top)
        print(analysis.format_summary(summary), end="\n\n")
        report["networks"].append(summary.to_dict())
    print("Geocode quality (unique stop names):", report["geocode_quality"])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {args.json}")


def cmd_map(args: argparse.Namespace) -> None:
    _, graph, _ = _load(args)
    out: Path = args.out_dir
    full = analysis.summarize(graph, top_k=10)
    title = "Maricá public bus network (EPT)"
    maps.save_map(maps.network_map(graph, title, _hubs(full)), out / "index.html", title)
    ita = subgraph_in_bbox(graph, REGIONS["itaipuacu"])
    ita_summary = analysis.summarize(ita, top_k=10)
    ita_title = "Itaipuaçu bus network (EPT)"
    maps.save_map(
        maps.network_map(ita, ita_title, _hubs(ita_summary)), out / "itaipuacu.html", ita_title
    )
    if not args.no_png:
        maps.static_network_png(
            graph,
            args.png,
            _hubs(full),
            f"Maricá (RJ) public bus network: {full.stops} stops, {full.edges} directed links,"
            f" {full.lines} lines (Moovit snapshot, 2025)",
        )
    print(f"maps written to {out}/")


def _resolve_stops(
    graph: nx.DiGraph, name: str | None, coord: tuple[float, float], label: str
) -> list[str]:
    if name:
        if name not in graph:
            raise SystemExit(f"{label} stop {name!r} is not in the selected network")
        return [name]
    candidates, dist = analysis.stops_near(graph, *coord)
    print(f"{label}: {len(candidates)} stop(s) {dist * 1000:.0f} m from {coord}: {candidates}")
    return candidates


def cmd_route(args: argparse.Namespace) -> None:
    _, graph, _ = _load(args)
    net = _region_graph(graph, args.region)
    sources = _resolve_stops(net, args.origin, args.from_coord, "origin")
    targets = _resolve_stops(net, args.destination, args.to_coord, "destination")
    try:
        route = analysis.best_route(net, sources, targets)
    except nx.NetworkXNoPath:
        raise SystemExit(f"no directed path from {sources} to {targets}") from None
    astar = analysis.shortest_route_astar(net, route.stops[0], route.stops[-1])
    print(f"\nDijkstra: {route.distance_km:.2f} km over {len(route.stops)} stops")
    print(f"A*:       {astar.distance_km:.2f} km over {len(astar.stops)} stops")
    for i, stop in enumerate(route.stops):
        lines = "" if i == 0 else f"  [{', '.join(route.leg_lines[i - 1])}]"
        print(f"  {i + 1:>2}. {stop}{lines}")
    rides = " -> ".join(f"{line} ({n} stops)" for line, n in route.rides)
    print(f"Fewest-transfer ride plan: {rides}  ({route.transfers} transfer(s))")
    if args.out:
        summary = analysis.summarize(net, top_k=10)
        title = f"Shortest route in {REGIONS[args.region].display_name}"
        maps.save_map(maps.route_map(net, route, title, _hubs(summary)), args.out, title)
        print(f"wrote {args.out}")


def cmd_coverage(args: argparse.Namespace) -> None:
    _, _, stops = _load(args)
    if args.region != "marica":
        box = REGIONS[args.region]
        stops = stops[[box.contains(a, b) for a, b in zip(stops["lat"], stops["lon"], strict=True)]]
    streets = tuple(args.street) if args.street else coverage.CARLOS_MARIGHELLA
    result = coverage.analyse_road(stops, streets, max_gap_m=args.max_gap_m)
    median = statistics.median(result.spacings_m) if result.spacings_m else 0.0
    print(f"Road: {', '.join(streets)}")
    print(
        f"  stop locations   {len(result.points)} ({sum(p.approximate for p in result.points)}"
        " with approximate geocode)"
    )
    print(f"  chained length   {result.road_length_km:.2f} km")
    print(f"  median spacing   {median:.0f} m")
    for label, n in result.class_counts().items():
        print(f"  {label:<24} {n}")
    print(f"  gaps > {result.max_gap_m:.0f} m       {len(result.gaps)} (one suggested stop each)")
    if args.out:
        label = streets[0]
        maps.save_map(maps.coverage_map(result, label), args.out, f"Stop spacing: {label}")
        print(f"wrote {args.out}")


def cmd_all(args: argparse.Namespace) -> None:
    """Regenerate every committed artefact (docs/ maps, preview PNG, stats JSON)."""
    docs: Path = args.out_dir
    for fn, extra in (
        (cmd_build_graph, {"graphml": Paths().graphml}),
        (
            cmd_analyze,
            {"region": ["marica", "itaipuacu"], "top": 10, "json": docs / "network_stats.json"},
        ),
        (cmd_map, {"no_png": False, "png": docs / "img" / "network-static.png"}),
        (
            cmd_route,
            {
                "region": "itaipuacu",
                "origin": None,
                "destination": None,
                "from_coord": DEFAULT_ORIGIN,
                "to_coord": DEFAULT_DESTINATION,
                "out": docs / "route-optimization.html",
            },
        ),
        (
            cmd_coverage,
            {
                "region": "itaipuacu",
                "street": None,
                "max_gap_m": 450.0,
                "out": docs / "main-road-coverage.html",
            },
        ),
    ):
        print(f"\n=== {fn.__name__.removeprefix('cmd_').replace('_', '-')} ===")
        fn(argparse.Namespace(**{**vars(args), **extra}))


# --- parser --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    data = common.add_argument_group("data source (offline by default)")
    data.add_argument("--data-dir", type=Path, default=Path("data"), help="default: data/")
    data.add_argument(
        "--force-rescrape",
        action="store_true",
        help="re-scrape Moovit, then re-geocode (network; needs the 'fetch' extra)",
    )
    data.add_argument(
        "--force-regeocode",
        action="store_true",
        help="re-geocode the raw snapshot (network; needs the 'fetch' extra)",
    )
    data.add_argument(
        "--geocoder",
        choices=["both", "nominatim", "google"],
        default="both",
        help="'both' = Nominatim first, Google fallback (needs GOOGLE_MAPS_API_KEY)",
    )
    data.add_argument("--contact", help="e-mail sent in the Nominatim User-Agent")
    common.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    parser = argparse.ArgumentParser(
        prog="marica-transit",
        description="Graph analysis and maps of the Maricá (RJ) public bus network.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("build-graph", parents=[common], help="build the graph, export GraphML")
    p.add_argument("--graphml", type=Path, default=Paths().graphml)
    p.set_defaults(func=cmd_build_graph)

    p = sub.add_parser("analyze", parents=[common], help="print network statistics")
    p.add_argument(
        "--region",
        choices=sorted(REGIONS),
        nargs="+",
        default=["marica", "itaipuacu"],
        help="network(s) to analyse (default: both)",
    )
    p.add_argument("--top", type=int, default=10, help="stops listed per ranking")
    p.add_argument("--json", type=Path, help="also write the statistics as JSON")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("map", parents=[common], help="write interactive maps and a PNG preview")
    p.add_argument("--out-dir", type=Path, default=Path("docs"))
    p.add_argument("--png", type=Path, default=Path("docs/img/network-static.png"))
    p.add_argument("--no-png", action="store_true")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("route", parents=[common], help="shortest route between two stops")
    p.add_argument("--region", choices=sorted(REGIONS), default="itaipuacu")
    p.add_argument("--from", dest="origin", metavar="STOP", help="origin stop name")
    p.add_argument("--to", dest="destination", metavar="STOP", help="destination stop name")
    p.add_argument("--from-coord", type=_coord, default=DEFAULT_ORIGIN, metavar="LAT,LON")
    p.add_argument("--to-coord", type=_coord, default=DEFAULT_DESTINATION, metavar="LAT,LON")
    p.add_argument("--out", type=Path, help="write an interactive route map (HTML)")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("coverage", parents=[common], help="stop-spacing gaps along a road")
    p.add_argument("--region", choices=sorted(REGIONS), default="itaipuacu")
    p.add_argument(
        "--street",
        action="append",
        help="street name to match (repeatable); default: Carlos Marighella",
    )
    p.add_argument("--max-gap-m", type=float, default=450.0)
    p.add_argument("--out", type=Path, help="write an interactive coverage map (HTML)")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("all", parents=[common], help="regenerate every artefact in docs/")
    p.add_argument("--out-dir", type=Path, default=Path("docs"))
    p.set_defaults(func=cmd_all)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()
