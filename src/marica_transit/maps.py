"""Interactive (Folium) and static (Matplotlib) maps of the network and case studies."""

from __future__ import annotations

import html
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import folium
import networkx as nx

from marica_transit.analysis import Route
from marica_transit.coverage import SPACING_CLASSES, CoverageResult, classify_spacing
from marica_transit.data import Quality

log = logging.getLogger(__name__)

TILES = "OpenStreetMap"
INK = "#0b0b0b"
MUTED = "#52514e"
EDGE = "#6f6e69"
STOP_OK = "#2a78d6"
STOP_APPROX = "#eb6834"
HUB = "#6250d6"
# Spacing classes keep the original project's traffic-light semantics, with
# palette-validated hexes; every class also carries a text label in tooltips and legend.
SPACING_COLOURS = {
    "green": "#008300",
    "gold": "#eda100",
    "orange": "#eb6834",
    "red": "#e34948",
    "purple": "#6250d6",
}
# Shared look of the floating legend / info panels.
PANEL_CSS = (
    "position:fixed;z-index:9999;background:rgba(255,255,255,.94);padding:10px 12px;"
    "border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.25);"
    f"font:12px/1.45 system-ui,sans-serif;color:{INK};"
)


def _panel(fmap: folium.Map, position_css: str, inner_html: str) -> None:
    div = f'<div style="{PANEL_CSS}{position_css}">{inner_html}</div>'
    fmap.get_root().html.add_child(folium.Element(div))


def _center(graph: nx.DiGraph) -> tuple[float, float]:
    lats = [d["lat"] for _, d in graph.nodes(data=True)]
    lons = [d["lon"] for _, d in graph.nodes(data=True)]
    return (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2


def _bounds(graph: nx.DiGraph) -> list[list[float]]:
    lats = [d["lat"] for _, d in graph.nodes(data=True)]
    lons = [d["lon"] for _, d in graph.nodes(data=True)]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def _base_map(graph: nx.DiGraph) -> folium.Map:
    fmap = folium.Map(location=_center(graph), zoom_start=12, tiles=TILES, control_scale=True)
    fmap.fit_bounds(_bounds(graph))
    return fmap


def _legend(fmap: folium.Map, title: str, items: Iterable[tuple[str, str]], note: str) -> None:
    rows = "".join(
        f'<div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;'
        f'background:{colour};margin-right:6px"></span>{html.escape(label)}</div>'
        for colour, label in items
    )
    _panel(
        fmap,
        "bottom:28px;right:12px;max-width:300px;",
        f'<div style="font-weight:600;margin-bottom:4px">{html.escape(title)}</div>{rows}'
        f'<div style="margin-top:6px;color:{MUTED}">{note}</div>',
    )


def _edge_features(graph: nx.DiGraph) -> list[dict[str, Any]]:
    features = []
    for u, v, d in graph.edges(data=True):
        a, b = graph.nodes[u], graph.nodes[v]
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]],
                },
                "properties": {
                    "link": html.escape(f"{u} → {v}"),
                    "length": f"{d['distance_km'] * 1000:.0f} m"
                    + (" (suspect: likely geocoding error)" if d["suspect"] else ""),
                    "lines": html.escape(", ".join(d["lines"])),
                    "n_lines": len(d["lines"]),
                    "suspect": d["suspect"],
                },
            }
        )
    return features


def _stop_features(graph: nx.DiGraph, quality: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
            "properties": {
                "stop": html.escape(n),
                "address": html.escape(d["address"]),
                "lines": html.escape(", ".join(d["lines"])),
                "geocode": d["quality"],
            },
        }
        for n, d in graph.nodes(data=True)
        if d["quality"] == quality
    ]


def _add_edges(fmap: folium.Map, graph: nx.DiGraph, colour: str = EDGE) -> None:
    folium.GeoJson(
        {"type": "FeatureCollection", "features": _edge_features(graph)},
        name="Stop-to-stop links",
        style_function=lambda f: {
            "color": colour,
            "weight": min(1.2 + 0.8 * f["properties"]["n_lines"], 6),
            "opacity": 0.3 if f["properties"]["suspect"] else 0.55,
            "dashArray": "4 6" if f["properties"]["suspect"] else None,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["link", "length", "lines"], aliases=["Link", "Length", "Lines"]
        ),
    ).add_to(fmap)


def _add_stops(fmap: folium.Map, graph: nx.DiGraph) -> None:
    for quality, colour, label in (
        (Quality.OK, STOP_OK, "Stops"),
        (Quality.APPROXIMATE, STOP_APPROX, "Stops with approximate geocode"),
    ):
        features = _stop_features(graph, quality)
        if not features:
            continue
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            name=label,
            marker=folium.CircleMarker(radius=4, weight=1, fill=True, fill_opacity=0.85),
            style_function=lambda _f, c=colour: {"color": "#ffffff", "fillColor": c},
            tooltip=folium.GeoJsonTooltip(fields=["stop"], labels=False),
            popup=folium.GeoJsonPopup(
                fields=["stop", "address", "lines", "geocode"],
                aliases=["Stop", "Geocoded address", "Lines", "Geocode"],
                max_width=320,
            ),
        ).add_to(fmap)


def _add_hubs(fmap: folium.Map, graph: nx.DiGraph, hubs: list[tuple[str, float]]) -> None:
    group = folium.FeatureGroup(name="Top stops by betweenness")
    for rank, (stop, score) in enumerate(hubs, 1):
        d = graph.nodes[stop]
        folium.CircleMarker(
            location=(d["lat"], d["lon"]),
            radius=9,
            color="#ffffff",
            weight=2,
            fill=True,
            fill_color=HUB,
            fill_opacity=0.9,
            tooltip=f"#{rank} {html.escape(stop)} (betweenness {score:.3f})",
            popup=folium.Popup(
                f"<b>#{rank} by betweenness</b><br>{html.escape(stop)}<br>"
                f"Betweenness: {score:.3f}<br>Lines: {html.escape(', '.join(d['lines']))}",
                max_width=320,
            ),
        ).add_to(group)
    group.add_to(fmap)


def network_map(
    graph: nx.DiGraph,
    title: str,
    hubs: list[tuple[str, float]] | None = None,
    layer_control: bool = True,
) -> folium.Map:
    """Interactive map of every stop and link; ``hubs`` are highlighted."""
    fmap = _base_map(graph)
    _add_edges(fmap, graph)
    _add_stops(fmap, graph)
    if hubs:
        _add_hubs(fmap, graph, hubs)
    if layer_control:  # must be added after every layer it lists
        folium.LayerControl(collapsed=True).add_to(fmap)
    _legend(
        fmap,
        title,
        [
            (STOP_OK, "Bus stop"),
            (STOP_APPROX, "Stop with approximate geocode"),
            *([(HUB, "Top-10 stop by betweenness centrality")] if hubs else []),
        ],
        f"{graph.number_of_nodes()} stops · {graph.number_of_edges()} links. "
        "Line width grows with the number of lines on a link; dashed links are > 5 km "
        "(likely geocoding errors). "
        "Data: Moovit snapshot (2025), geocoded with Nominatim/Google.",
    )
    return fmap


def route_map(
    graph: nx.DiGraph, route: Route, title: str, hubs: list[tuple[str, float]] | None = None
) -> folium.Map:
    """Network map with a shortest route drawn on top."""
    fmap = network_map(graph, title, hubs, layer_control=False)
    coords = [(graph.nodes[n]["lat"], graph.nodes[n]["lon"]) for n in route.stops]
    rides = " → ".join(
        f"{line} ({legs} stop{'s' if legs > 1 else ''})" for line, legs in route.rides
    )
    group = folium.FeatureGroup(name="Shortest route")
    folium.PolyLine(
        coords,
        color=INK,
        weight=5,
        opacity=0.9,
        tooltip=f"Shortest route: {route.distance_km:.2f} km, {len(route.stops)} stops",
    ).add_to(group)
    for label, stop in (("Origin", route.stops[0]), ("Destination", route.stops[-1])):
        d = graph.nodes[stop]
        folium.Marker(
            (d["lat"], d["lon"]),
            tooltip=f"{label}: {html.escape(stop)}",
            icon=folium.Icon(color="black", icon="flag" if label == "Destination" else "play"),
        ).add_to(group)
    group.add_to(fmap)
    folium.LayerControl(collapsed=True).add_to(fmap)
    _panel(
        fmap,
        "top:12px;left:56px;max-width:420px;",
        f"<b>Shortest route</b> {html.escape(route.stops[0])} → "
        f"{html.escape(route.stops[-1])}<br>{route.distance_km:.2f} km · "
        f"{len(route.stops)} stops · {route.transfers} transfer(s)<br>"
        f"Rides: {html.escape(rides)}",
    )
    return fmap


def coverage_map(result: CoverageResult, road_label: str) -> folium.Map:
    """Stops along one road, links coloured by spacing class, suggested stops at gaps."""
    lats = [p.lat for p in result.points]
    lons = [p.lon for p in result.points]
    fmap = folium.Map(
        location=(sum(lats) / len(lats), sum(lons) / len(lons)),
        zoom_start=14,
        tiles=TILES,
        control_scale=True,
    )
    fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
    for a, b, dist in zip(result.points, result.points[1:], result.spacings_m, strict=False):
        label, colour = classify_spacing(dist)
        folium.PolyLine(
            [(a.lat, a.lon), (b.lat, b.lon)],
            color=SPACING_COLOURS[colour],
            weight=5,
            opacity=0.85,
            tooltip=f"{dist:.0f} m: {label}",
        ).add_to(fmap)
    for p in result.points:
        names = "<br>".join(html.escape(n) for n in p.stop_names)
        folium.CircleMarker(
            (p.lat, p.lon),
            radius=6,
            color="#ffffff",
            weight=1.5,
            fill=True,
            fill_color=STOP_APPROX if p.approximate else STOP_OK,
            fill_opacity=0.9,
            tooltip=html.escape(p.stop_names[0]),
            popup=folium.Popup(
                f"<b>Stop(s)</b><br>{names}<br><b>Lines</b>: {html.escape(', '.join(p.lines))}"
                + ("<br><i>approximate geocode</i>" if p.approximate else ""),
                max_width=320,
            ),
        ).add_to(fmap)
    for gap in result.gaps:
        folium.Marker(
            gap.midpoint,
            tooltip=f"Suggested stop: {gap.distance_m:.0f} m gap",
            icon=folium.Icon(color="black", icon="plus"),
        ).add_to(fmap)
    _legend(
        fmap,
        f"Stop spacing along {road_label}",
        [(SPACING_COLOURS[c], label) for _, label, c in SPACING_CLASSES]
        + [(INK, f"Suggested stop (gap > {result.max_gap_m:.0f} m)")],
        f"{len(result.points)} stop locations · {result.road_length_km:.1f} km of road · "
        f"{len(result.gaps)} gaps above {result.max_gap_m:.0f} m.",
    )
    return fmap


def save_map(fmap: folium.Map, path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmap.get_root().title = title
    fmap.save(str(path))
    log.info("wrote %s (%.0f KB)", path, path.stat().st_size / 1024)


def static_network_png(
    graph: nx.DiGraph,
    path: Path,
    hubs: list[tuple[str, float]],
    title: str,
    label_top: int = 5,
) -> None:
    """Static preview of the network over lon/lat (no basemap, so it renders offline)."""
    import math  # noqa: PLC0415

    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    from matplotlib.collections import LineCollection  # noqa: PLC0415
    from matplotlib.lines import Line2D  # noqa: PLC0415

    solid, solid_w, dashed = [], [], []
    for u, v, d in graph.edges(data=True):
        a, b = graph.nodes[u], graph.nodes[v]
        seg = [(a["lon"], a["lat"]), (b["lon"], b["lat"])]
        if d["suspect"]:
            dashed.append(seg)
        else:
            solid.append(seg)
            solid_w.append(min(0.6 + 0.4 * len(d["lines"]), 3.2))

    fig, ax = plt.subplots(figsize=(12, 5.4), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    ax.add_collection(
        LineCollection(dashed, colors=EDGE, linewidths=0.5, alpha=0.18, linestyles=(0, (3, 3)))
    )
    ax.add_collection(LineCollection(solid, colors=EDGE, linewidths=solid_w, alpha=0.7))
    for quality, colour in ((Quality.OK, STOP_OK), (Quality.APPROXIMATE, STOP_APPROX)):
        pts = [(d["lon"], d["lat"]) for _, d in graph.nodes(data=True) if d["quality"] == quality]
        if pts:
            xs, ys = zip(*pts, strict=True)
            ax.scatter(xs, ys, s=10, color=colour, edgecolors="white", linewidths=0.3, zorder=3)
    # Several hubs can sit on one (approximate) coordinate: label them together.
    at_coord: dict[tuple[float, float], list[int]] = {}
    for rank, (stop, _score) in enumerate(hubs[:label_top], 1):
        d = graph.nodes[stop]
        at_coord.setdefault((d["lon"], d["lat"]), []).append(rank)
    for (x, y), ranks in at_coord.items():
        label = ",".join(map(str, ranks))
        ax.scatter(
            x, y, s=150 + 60 * (len(label) - 1), color=HUB, edgecolors="white", lw=1.2, zorder=4
        )
        ax.annotate(
            label,
            (x, y),
            ha="center",
            va="center",
            fontsize=7,
            fontweight="bold",
            color="white",
            zorder=5,
        )
    ranking = "\n".join(f"{i}. {stop}" for i, (stop, _) in enumerate(hubs[:label_top], 1))
    ax.text(
        0.995,
        0.99,
        f"Top {label_top} stops by betweenness\n{ranking}",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=7.5,
        color=INK,
        linespacing=1.45,
        bbox={"boxstyle": "round,pad=0.5", "fc": "white", "ec": "#d9d8d4", "alpha": 0.92},
        zorder=6,
    )
    mean_lat = sum(d["lat"] for _, d in graph.nodes(data=True)) / graph.number_of_nodes()
    ax.set_aspect(1 / math.cos(math.radians(mean_lat)))
    ax.autoscale_view()
    ax.margins(0.02)
    ax.set_xlabel("Longitude", color=MUTED, fontsize=9)
    ax.set_ylabel("Latitude", color=MUTED, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#d9d8d4")
    ax.set_title(title, loc="left", fontsize=11.5, color=INK, pad=10)
    ax.legend(
        handles=[
            Line2D([], [], marker="o", ls="", color=STOP_OK, label="Bus stop"),
            Line2D([], [], marker="o", ls="", color=STOP_APPROX, label="Approximate geocode"),
            Line2D([], [], marker="o", ls="", color=HUB, ms=9, label="Top betweenness"),
            Line2D([], [], color=EDGE, lw=2, label="Stop-to-stop link"),
            Line2D(
                [], [], color=EDGE, lw=1, ls=(0, (3, 3)), alpha=0.5, label="Link > 5 km (suspect)"
            ),
        ],
        loc="upper left",
        bbox_to_anchor=(0, -0.13),
        ncol=5,
        fontsize=8,
        frameon=False,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    log.info("wrote %s", path)
