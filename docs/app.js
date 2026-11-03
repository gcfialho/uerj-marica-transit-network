// Maricá bus network explorer. Reads data/network.json (written by `marica-transit web`)
// and draws it with MapLibre. All analysis numbers come from the Python package; the page
// only filters, routes (Dijkstra, same rules as the package) and draws.
import { STRINGS } from "./i18n.js";

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const STYLES = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};
// Spacing classes from coverage.SPACING_CLASSES, drawn with a calmer palette.
const CLASS_COLORS = { green: "#1f9e77", gold: "#c9a227", orange: "#e07b29", red: "#d1453b", purple: "#7b3fa0" };

const state = {
  lang: initialLang(),
  tab: "overview",
  region: "marica",
  mode: "lines",
  line: null, // line code
  stop: null, // stop id
  focus: null, // stop id highlighted inside a line or route view
  route: { from: null, to: null, isCase: false, picking: null, result: null },
  corridorMax: 450,
};

let data, map, popup;
let stopsById = [], adjacency = [], lineByCode = new Map(), btRank = [];
const panel = document.querySelector("[data-view]");
const legendEl = document.querySelector("[data-legend]");
const pickBanner = document.querySelector("[data-pick-banner]");

/* ---------------------------------------------------------------- helpers */

function initialLang() {
  const fromUrl = new URLSearchParams(location.search).get("lang");
  if (fromUrl === "pt" || fromUrl === "en") return fromUrl;
  try {
    const saved = localStorage.getItem("marica-lang");
    if (saved === "pt" || saved === "en") return saved;
  } catch {}
  return (navigator.language || "pt").toLowerCase().startsWith("pt") ? "pt" : "en";
}
const T = () => STRINGS[state.lang];
const locale = () => (state.lang === "pt" ? "pt-BR" : "en-US");
const fmt = (n, digits = 0) =>
  new Intl.NumberFormat(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const lookup = (path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), T());
const isNarrow = () => window.matchMedia("(max-width: 52rem)").matches;

function haversineKm(a, b) {
  const R = 6371.0088, r = Math.PI / 180;
  const dphi = (b.lat - a.lat) * r, dl = (b.lon - a.lon) * r;
  const h = Math.sin(dphi / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/* ---------------------------------------------------------------- data */

function index() {
  stopsById = data.stops;
  adjacency = data.stops.map(() => []);
  for (const e of data.edges) adjacency[e.s].push(e);
  for (const line of data.lines) lineByCode.set(line.code, line);
  const ranked = [...data.stops].sort((a, b) => b.bt - a.bt);
  btRank = [];
  ranked.forEach((s, i) => (btRank[s.id] = i + 1));

  // Fit to where the stops actually are (2nd to 98th percentile), not the generous
  // validation envelope, so a few far-off geocodes do not zoom the map out.
  const extent = (list) => {
    const q = (arr, p) => arr[Math.min(arr.length - 1, Math.max(0, Math.round(p * (arr.length - 1))))];
    const lons = list.map((s) => s.lon).sort((x, y) => x - y), lats = list.map((s) => s.lat).sort((x, y) => x - y);
    return [[q(lons, 0.02), q(lats, 0.02)], [q(lons, 0.98), q(lats, 0.98)]];
  };
  data.view = { marica: extent(data.stops), itaipuacu: extent(data.stops.filter((s) => s.bt_ita !== undefined)) };

  // Present the corridor west to east.
  const c = data.corridor;
  if (c.points.length > 1 && c.points[0].lon > c.points[c.points.length - 1].lon) {
    c.points.reverse();
    c.spacings_m.reverse();
  }
}

const hop = (a, b) => adjacency[a].find((x) => x.t === b);
// Length over trustworthy hops only; hops over 5 km are geocoding errors (flagged suspect).
function lineKm(line) {
  let km = 0, suspect = 0;
  for (let i = 1; i < line.stops.length; i++) {
    const e = hop(line.stops[i - 1], line.stops[i]);
    if (!e) continue;
    if (e.suspect) suspect += 1;
    else km += e.km;
  }
  return { km, suspect };
}

// Dijkstra over link distances, following line direction (same weights as the package).
function shortestPath(from, to) {
  const n = stopsById.length;
  const dist = new Float64Array(n).fill(Infinity);
  const prev = new Int32Array(n).fill(-1);
  const done = new Uint8Array(n);
  dist[from] = 0;
  const heap = [[0, from]];
  const push = (item) => {
    heap.push(item);
    let i = heap.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (heap[p][0] <= heap[i][0]) break;
      [heap[p], heap[i]] = [heap[i], heap[p]];
      i = p;
    }
  };
  const pop = () => {
    const top = heap[0], last = heap.pop();
    if (heap.length) {
      heap[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
        if (m === i) break;
        [heap[m], heap[i]] = [heap[i], heap[m]];
        i = m;
      }
    }
    return top;
  };
  while (heap.length) {
    const [d, u] = pop();
    if (done[u]) continue;
    done[u] = 1;
    if (u === to) break;
    for (const e of adjacency[u]) {
      const nd = d + e.km;
      if (nd < dist[e.t]) {
        dist[e.t] = nd;
        prev[e.t] = u;
        push([nd, e.t]);
      }
    }
  }
  if (!Number.isFinite(dist[to])) return null;
  const path = [];
  for (let v = to; v !== -1; v = prev[v]) path.unshift(v);
  return describePath(path);
}

function describePath(path) {
  const legLines = [];
  let km = 0;
  for (let i = 1; i < path.length; i++) {
    const e = adjacency[path[i - 1]].find((x) => x.t === path[i]);
    legLines.push(e.lines);
    km += e.km;
  }
  return { stops: path, km, legLines, rides: planRides(legLines) };
}

// Port of analysis.plan_rides: stay on the lines that serve every leg so far.
function planRides(legLines) {
  const rides = [];
  let current = new Set(), count = 0;
  for (const options of legLines) {
    const narrowed = new Set(options.filter((l) => current.has(l)));
    if (narrowed.size) {
      current = narrowed;
      count += 1;
      continue;
    }
    if (current.size) rides.push([[...current].sort()[0], count]);
    current = new Set(options);
    count = 1;
  }
  if (current.size) rides.push([[...current].sort()[0], count]);
  return rides;
}

function corridorView() {
  const c = data.corridor;
  const gaps = c.spacings_m
    .map((d, i) => ({ d, i, a: c.points[i], b: c.points[i + 1] }))
    .filter((g) => g.d > state.corridorMax);
  return { c, gaps };
}

function classOf(d) {
  for (let i = 0; i < data.corridor.classes.length; i++) {
    const [upper, colour] = data.corridor.classes[i];
    if (upper === null || d <= upper) return { i, color: CLASS_COLORS[colour] || "#888" };
  }
  return { i: 0, color: "#888" };
}

/* ---------------------------------------------------------------- map */

function geo() {
  const inIta = (s) => s.bt_ita !== undefined;
  return {
    stops: {
      type: "FeatureCollection",
      features: data.stops.map((s) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        properties: { id: s.id, n: s.lines.length, bt: s.bt, bt_ita: s.bt_ita ?? 0, q: s.quality, ita: inIta(s) },
      })),
    },
    edges: {
      type: "FeatureCollection",
      features: data.edges.map((e) => {
        const a = stopsById[e.s], b = stopsById[e.t];
        return {
          type: "Feature",
          geometry: { type: "LineString", coordinates: [[a.lon, a.lat], [b.lon, b.lat]] },
          properties: { n: e.lines.length, suspect: e.suspect, ita: inIta(a) && inIta(b) },
        };
      }),
    },
  };
}

const empty = () => ({ type: "FeatureCollection", features: [] });
const bboxPolygon = ([[w, s], [e, n]]) => ({
  type: "Feature",
  geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
  properties: {},
});

function addLayers() {
  const g = geo();
  const ink = css("--ink"), accent = css("--accent"), flag = css("--flag"), edge = css("--edge"), paper = css("--surface");
  map.addSource("edges", { type: "geojson", data: g.edges });
  map.addSource("stops", { type: "geojson", data: g.stops, promoteId: "id" });
  map.addSource("region", { type: "geojson", data: bboxPolygon(data.regions.itaipuacu.bounds) });
  for (const id of ["hl", "hl-stops", "sel", "od", "corridor-seg", "corridor-pts", "corridor-gaps"]) {
    map.addSource(id, { type: "geojson", data: empty() });
  }

  const width = ["interpolate", ["linear"], ["zoom"], 10, ["+", 0.5, ["*", 0.12, ["get", "n"]]], 15, ["+", 1.4, ["*", 0.5, ["get", "n"]]]];
  map.addLayer({ id: "region", type: "line", source: "region", paint: { "line-color": accent, "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.8 }, layout: { visibility: "none" } });
  map.addLayer({ id: "edges", type: "line", source: "edges", filter: ["!", ["get", "suspect"]], layout: { "line-cap": "round" }, paint: { "line-color": edge, "line-width": width } });
  map.addLayer({ id: "edges-suspect", type: "line", source: "edges", filter: ["get", "suspect"], paint: { "line-color": flag, "line-width": 1.2, "line-dasharray": [2, 2], "line-opacity": 0.6 } });
  map.addLayer({ id: "corridor-seg", type: "line", source: "corridor-seg", layout: { "line-cap": "round" }, paint: { "line-color": ["get", "color"], "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 9, 6] } });
  map.addLayer({ id: "hl-casing", type: "line", source: "hl", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": paper, "line-width": 8 } });
  map.addLayer({ id: "hl-line", type: "line", source: "hl", filter: ["!", ["get", "suspect"]], layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": accent, "line-width": 4.5 } });
  map.addLayer({ id: "hl-suspect", type: "line", source: "hl", filter: ["get", "suspect"], paint: { "line-color": flag, "line-width": 3, "line-dasharray": [1.5, 1.5] } });
  map.addLayer({ id: "hl-flow", type: "line", source: "hl", filter: ["!", ["get", "suspect"]], layout: { "line-cap": "butt", "line-join": "round" }, paint: { "line-color": paper, "line-width": 2, "line-opacity": 0.85, "line-dasharray": [0, 4, 3] } });
  map.addLayer({ id: "stops", type: "circle", source: "stops", paint: { "circle-color": ink, "circle-stroke-color": paper, "circle-stroke-width": ["case", ["boolean", ["feature-state", "hover"], false], 2.5, 0.8] } });
  map.addLayer({ id: "hl-stops", type: "circle", source: "hl-stops", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 3, 15, 6], "circle-color": paper, "circle-stroke-color": accent, "circle-stroke-width": 2 } });
  map.addLayer({ id: "corridor-gaps", type: "circle", source: "corridor-gaps", paint: { "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": flag, "circle-stroke-width": 2.5 } });
  map.addLayer({ id: "corridor-gaps-core", type: "circle", source: "corridor-gaps", paint: { "circle-radius": 3, "circle-color": flag } });
  map.addLayer({ id: "corridor-pts", type: "circle", source: "corridor-pts", paint: { "circle-radius": 5, "circle-color": paper, "circle-stroke-color": ["case", ["get", "approximate"], flag, ink], "circle-stroke-width": 2 } });
  map.addLayer({ id: "od", type: "circle", source: "od", paint: { "circle-radius": 8, "circle-color": ["match", ["get", "role"], "from", accent, paper], "circle-stroke-color": ["match", ["get", "role"], "from", paper, accent], "circle-stroke-width": 3 } });
  map.addLayer({ id: "sel-pulse", type: "circle", source: "sel", paint: { "circle-radius": 8, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": accent, "circle-stroke-width": 2, "circle-stroke-opacity": 0 } });
  map.addLayer({ id: "sel", type: "circle", source: "sel", paint: { "circle-radius": 7, "circle-color": accent, "circle-stroke-color": paper, "circle-stroke-width": 3 } });

  bindMapEvents();
  syncMap({ fit: false });
}

let eventsBound = false;
let hovered = null;
function bindMapEvents() {
  if (eventsBound) return;
  eventsBound = true;
  popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, maxWidth: "260px" });

  map.on("mousemove", "stops", (e) => {
    const f = e.features[0];
    if (!f) return;
    map.getCanvas().style.cursor = "pointer";
    if (hovered !== null) map.setFeatureState({ source: "stops", id: hovered }, { hover: false });
    hovered = f.properties.id;
    map.setFeatureState({ source: "stops", id: hovered }, { hover: true });
    const s = stopsById[hovered];
    popup
      .setLngLat([s.lon, s.lat])
      .setHTML(`<div class="popup-name">${esc(s.name)}</div><div class="popup-meta">${esc(T().stop.lines)}: ${s.lines.map(esc).join(", ")}</div>`)
      .addTo(map);
  });
  map.on("mouseleave", "stops", () => {
    map.getCanvas().style.cursor = "";
    if (hovered !== null) map.setFeatureState({ source: "stops", id: hovered }, { hover: false });
    hovered = null;
    popup.remove();
  });
  map.on("click", "stops", (e) => {
    const id = e.features[0]?.properties.id;
    if (id == null) return;
    if (state.route.picking) {
      const which = state.route.picking;
      state.route.picking = null;
      state.route[which] = id;
      state.route.isCase = false;
      goRoute();
      return;
    }
    go(`#/stops/${id}`);
  });

  map.on("mousemove", "corridor-seg", (e) => {
    const i = e.features[0]?.properties.i;
    if (i != null) hoverSegment(i);
  });
  map.on("mouseleave", "corridor-seg", () => hoverSegment(null));
}

let hoveredSeg = null;
function hoverSegment(i) {
  if (hoveredSeg !== null && map.getSource("corridor-seg")) map.setFeatureState({ source: "corridor-seg", id: hoveredSeg }, { hover: false });
  hoveredSeg = i;
  if (i !== null && map.getSource("corridor-seg")) map.setFeatureState({ source: "corridor-seg", id: i }, { hover: true });
  document.querySelectorAll(".spacing-chart .bar").forEach((b) => b.classList.toggle("is-dim", i !== null && Number(b.dataset.i) !== i));
}

function stopPaint() {
  const dark = darkQuery.matches;
  const key = state.region === "itaipuacu" ? "bt_ita" : "bt";
  const zoomed = (r) => ["interpolate", ["linear"], ["zoom"], 10, ["*", 0.75, r], 14, ["*", 1.5, r]];
  if (state.mode === "centrality") {
    const lo = dark ? "#33404f" : "#c4ccd5";
    const mid = dark ? "#2f8f7c" : "#5aa596";
    const hi = dark ? "#8af2d8" : "#064a3f";
    return {
      color: ["interpolate", ["linear"], ["get", key], 0, lo, 0.08, mid, 0.3, hi],
      radius: zoomed(["+", 1.8, ["*", 22, ["get", key]]]),
      sort: ["get", key],
    };
  }
  if (state.mode === "quality") {
    return { color: ["match", ["get", "q"], "approximate", css("--flag"), css("--ink")], radius: zoomed(2.6), sort: ["match", ["get", "q"], "approximate", 1, 0] };
  }
  return { color: css("--ink"), radius: zoomed(["+", 1.4, ["*", 1.15, ["sqrt", ["get", "n"]]]]), sort: ["get", "n"] };
}

function syncMap({ fit = true } = {}) {
  if (!map || !map.getLayer("stops")) return;
  const paint = stopPaint();
  map.setPaintProperty("stops", "circle-color", paint.color);
  map.setPaintProperty("stops", "circle-radius", paint.radius);
  map.setLayoutProperty("stops", "circle-sort-key", paint.sort);

  const focusMode = (state.tab === "lines" && state.line) || (state.tab === "route" && state.route.result) || state.tab === "corridor";
  const regionOut = state.region === "itaipuacu" ? ["!", ["get", "ita"]] : false;
  const base = focusMode ? 0.28 : 1;
  const op = (on) => (regionOut ? ["case", regionOut, Math.min(0.12, on), on] : on);
  map.setPaintProperty("stops", "circle-opacity", op(base * 0.9));
  map.setPaintProperty("stops", "circle-stroke-opacity", op(base));
  map.setPaintProperty("edges", "line-opacity", op(focusMode ? 0.35 : 0.8));
  map.setLayoutProperty("edges-suspect", "visibility", state.mode === "quality" && !focusMode ? "visible" : "none");
  map.setLayoutProperty("region", "visibility", state.region === "itaipuacu" || state.tab === "corridor" ? "visible" : "none");

  // Highlighted path: a line's sequence or the computed route.
  let path = null;
  if (state.tab === "lines" && state.line) path = lineByCode.get(state.line)?.stops;
  if (state.tab === "route" && state.route.result) path = state.route.result.stops;
  const hops = [];
  if (path) {
    for (let i = 1; i < path.length; i++) {
      const a = stopsById[path[i - 1]], b = stopsById[path[i]];
      hops.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[a.lon, a.lat], [b.lon, b.lat]] }, properties: { suspect: Boolean(hop(a.id, b.id)?.suspect) } });
    }
  }
  map.getSource("hl").setData({ type: "FeatureCollection", features: hops });
  map.getSource("hl-stops").setData(
    path ? { type: "FeatureCollection", features: path.map((id) => ({ type: "Feature", geometry: { type: "Point", coordinates: [stopsById[id].lon, stopsById[id].lat] }, properties: { id } })) } : empty(),
  );
  path ? startFlow() : stopFlow();

  // Origin and destination markers.
  const od = [];
  if (state.tab === "route") {
    for (const role of ["from", "to"]) {
      const id = state.route[role];
      if (id != null) od.push({ type: "Feature", geometry: { type: "Point", coordinates: [stopsById[id].lon, stopsById[id].lat] }, properties: { role } });
    }
  }
  map.getSource("od").setData({ type: "FeatureCollection", features: od });

  // Selected or focused stop.
  const sel = state.tab === "stops" ? state.stop : state.focus;
  map.getSource("sel").setData(sel != null ? { type: "Feature", geometry: { type: "Point", coordinates: [stopsById[sel].lon, stopsById[sel].lat] }, properties: {} } : empty());

  syncCorridor();
  renderLegend();
  if (fit) fitToState();
}

function syncCorridor() {
  const on = state.tab === "corridor";
  const { c, gaps } = corridorView();
  map.getSource("corridor-seg").setData(
    on
      ? {
          type: "FeatureCollection",
          features: c.spacings_m.map((d, i) => ({
            type: "Feature",
            id: i,
            geometry: { type: "LineString", coordinates: [[c.points[i].lon, c.points[i].lat], [c.points[i + 1].lon, c.points[i + 1].lat]] },
            properties: { i, color: classOf(d).color },
          })),
        }
      : empty(),
  );
  map.getSource("corridor-pts").setData(
    on ? { type: "FeatureCollection", features: c.points.map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { approximate: p.approximate } })) } : empty(),
  );
  map.getSource("corridor-gaps").setData(
    on
      ? {
          type: "FeatureCollection",
          features: gaps.map((g) => ({ type: "Feature", geometry: { type: "Point", coordinates: [(g.a.lon + g.b.lon) / 2, (g.a.lat + g.b.lat) / 2] }, properties: {} })),
        }
      : empty(),
  );
}

function padding() {
  return isNarrow() ? { top: 30, bottom: 30, left: 24, right: 24 } : { top: 70, bottom: 70, left: 70, right: 70 };
}
function fitCoords(coords, maxZoom = 15) {
  if (!coords.length) return;
  const b = new maplibregl.LngLatBounds(coords[0], coords[0]);
  coords.forEach((c) => b.extend(c));
  map.fitBounds(b, { padding: padding(), maxZoom, duration: reduceMotion ? 0 : 900 });
}
function flyToStop(id, zoom = 15) {
  const s = stopsById[id];
  const opts = { center: [s.lon, s.lat], zoom: Math.max(map.getZoom(), zoom) };
  reduceMotion ? map.jumpTo(opts) : map.flyTo({ ...opts, speed: 1.4, curve: 1.3 });
}
function fitToState() {
  if (state.tab === "lines" && state.line) return fitCoords(lineByCode.get(state.line).stops.map((id) => [stopsById[id].lon, stopsById[id].lat]));
  if (state.tab === "route" && state.route.result) return fitCoords(state.route.result.stops.map((id) => [stopsById[id].lon, stopsById[id].lat]));
  if (state.tab === "stops" && state.stop != null) return flyToStop(state.stop);
  if (state.tab === "corridor") return fitCoords(data.corridor.points.map((p) => [p.lon, p.lat]), 15);
  const box = data.view[state.region];
  map.fitBounds(box, { padding: padding(), duration: reduceMotion ? 0 : 900 });
}

// The flowing dashes show the direction of travel along a highlighted path.
const DASHES = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
let flowFrame = 0, flowStep = -1;
function startFlow() {
  if (reduceMotion || flowFrame) return;
  const tick = (ts) => {
    const step = Math.floor(ts / 55) % DASHES.length;
    if (step !== flowStep && map.getLayer("hl-flow")) {
      map.setPaintProperty("hl-flow", "line-dasharray", DASHES[step]);
      flowStep = step;
    }
    flowFrame = requestAnimationFrame(tick);
  };
  flowFrame = requestAnimationFrame(tick);
}
function stopFlow() {
  cancelAnimationFrame(flowFrame);
  flowFrame = 0;
}

// A ring that ripples out twice from a newly selected stop.
function pulseSelection() {
  if (reduceMotion || !map.getLayer("sel-pulse")) return;
  const start = performance.now();
  const frame = (now) => {
    const t = (now - start) / 1100;
    if (t >= 2 || !map.getLayer("sel-pulse")) return map.getLayer("sel-pulse") && map.setPaintProperty("sel-pulse", "circle-stroke-opacity", 0);
    const k = t % 1;
    const eased = 1 - Math.pow(1 - k, 3);
    map.setPaintProperty("sel-pulse", "circle-radius", 8 + 18 * eased);
    map.setPaintProperty("sel-pulse", "circle-stroke-opacity", 0.7 * (1 - k));
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

/* ---------------------------------------------------------------- legend */

function renderLegend() {
  const L = T().legend;
  const dark = darkQuery.matches;
  let rows = [];
  if (state.tab === "corridor") {
    rows = data.corridor.classes.map(([, colour], i) => `<div class="row"><span class="dot" style="background:${CLASS_COLORS[colour]}"></span>${esc(T().corridor.classes[i])}</div>`);
    rows.push(`<div class="row"><span class="dot" style="border:2px solid var(--flag)"></span>${esc(L.gapStop)}</div>`);
  } else if ((state.tab === "lines" && state.line) || (state.tab === "route" && state.route.result)) {
    rows.push(`<div class="row"><span class="dash" style="border-top:4px solid var(--accent);width:1.6rem"></span>${esc(L.selected)}</div>`);
    rows.push(`<div class="row"><span class="dash" style="border-top:3px dashed var(--flag);width:1.6rem"></span>${esc(L.suspect)}</div>`);
  } else if (state.mode === "centrality") {
    const ramp = dark ? "#33404f, #2f8f7c, #8af2d8" : "#c4ccd5, #5aa596, #064a3f";
    rows.push(`<div class="muted">${esc(L.centrality)}</div>`);
    rows.push(`<div class="row"><span>${esc(L.low)}</span><span class="ramp" style="background:linear-gradient(90deg, ${ramp})"></span><span>${esc(L.high)}</span></div>`);
  } else if (state.mode === "quality") {
    rows.push(`<div class="row"><span class="dot" style="background:var(--ink)"></span>${esc(L.ok)}</div>`);
    rows.push(`<div class="row"><span class="dot" style="background:var(--flag)"></span>${esc(L.approximate)}</div>`);
    rows.push(`<div class="row"><span class="dash" style="border-top-color:var(--flag)"></span>${esc(L.suspect)}</div>`);
  } else {
    rows.push(`<div class="muted">${esc(L.lines)}</div>`);
    rows.push(`<div class="muted">${esc(L.edgeWidth)}</div>`);
  }
  legendEl.innerHTML = rows.join("");
}

/* ---------------------------------------------------------------- views */

function segmented(name, value, options) {
  return `<div class="segmented" role="group">${options
    .map(([v, label]) => `<button type="button" data-action="${name}" data-value="${v}" data-focus-key="${name}-${v}" aria-pressed="${v === value}">${esc(label)}</button>`)
    .join("")}</div>`;
}

function viewOverview() {
  const t = T(), s = data.summaries[state.region], m = data.summaries.marica;
  const pct = (x, d = 1) => `${fmt(x * 100, d)}%`;
  const top = s.top_betweenness;
  const max = top[0]?.value || 1;
  const hub = m.top_lines_served[0];
  const corridorPct = Math.round((m.top_betweenness.slice(0, 3).reduce((a, r) => a + r.value, 0) / 3) * 100);
  return `
    <section class="section">
      <p class="lede">${esc(t.lede(m))}</p>
    </section>
    <section class="section">
      <span class="field-label">${esc(t.region)}</span>
      ${segmented("region", state.region, [["marica", t.regions.marica], ["itaipuacu", t.regions.itaipuacu]])}
    </section>
    <section class="section">
      <h2>${esc(t.statsTitle)}: ${esc(t.regions[state.region])}</h2>
      <dl class="stats">
        <div><dt>${esc(t.stats.stops)}</dt><dd>${fmt(s.stops)}</dd></div>
        <div><dt>${esc(t.stats.edges)}</dt><dd>${fmt(s.edges)}</dd></div>
        <div><dt>${esc(t.stats.lines)}</dt><dd>${fmt(s.lines)}</dd></div>
        <div><dt>${esc(t.stats.reciprocity)}</dt><dd>${pct(s.reciprocity)}</dd></div>
        <div><dt>${esc(t.stats.scc)}</dt><dd>${esc(t.stats.sccValue(fmt(s.largest_strong_component), fmt(s.stops)))}</dd></div>
        <div><dt>${esc(t.stats.avgHops)}</dt><dd>${esc(t.stats.hops(fmt(s.scc_avg_shortest_path_hops, 1)))}</dd></div>
        <div><dt>${esc(t.stats.diameter)}</dt><dd>${esc(t.stats.hops(fmt(s.scc_diameter_hops)))}</dd></div>
        <div><dt>${esc(t.stats.medianClean)}<small>${esc(t.stats.medianCleanNote)}</small></dt><dd>${fmt(s.median_clean_edge_km * 1000)} ${t.m}</dd></div>
      </dl>
    </section>
    <section class="section">
      <span class="field-label">${esc(t.colorBy)}</span>
      ${segmented("mode", state.mode, [["lines", t.modes.lines], ["centrality", t.modes.centrality], ["quality", t.modes.quality]])}
    </section>
    <section class="section">
      <h2>${esc(t.topTitle)}</h2>
      <p class="note">${esc(t.topNote)}</p>
      <ol class="rank">
        ${top
          .map(
            (r, i) => `<li><button type="button" data-action="stop" data-id="${r.id}">
              <span class="pos">${i + 1}</span><span class="name">${esc(r.stop)}</span><span class="val">${fmt(r.value, 3)}</span>
              <span class="bar"><i style="transform:scaleX(${(r.value / max).toFixed(3)})"></i></span></button></li>`,
          )
          .join("")}
      </ol>
    </section>
    <section class="section">
      <h2>${esc(t.findingsTitle)}</h2>
      <ul class="findings">
        <li>${esc(t.findings.hub(hub.stop, hub.lines, m.lines))}</li>
        <li>${esc(t.findings.corridor(corridorPct))}</li>
        <li>${esc(t.findings.direction(fmt(m.reciprocity * 100, 1), m.stops - m.largest_strong_component))}</li>
        <li>${esc(t.findings.audit)}</li>
      </ul>
    </section>
    <section class="section">
      <h2>${esc(t.methodTitle)}</h2>
      <p class="note">${esc(t.method)}</p>
      <p class="note" style="margin-top:.5rem"><a href="https://github.com/gcfialho/uerj-marica-transit-network#methodology">${esc(t.methodLink)}</a> ·
        <a href="https://github.com/gcfialho/uerj-marica-transit-network/tree/main/article">${esc(t.articleLink)}</a></p>
    </section>`;
}

function lineItems(query) {
  const q = norm(query.trim());
  const list = data.lines.filter((l) => !q || norm(l.code).includes(q) || norm(l.name).includes(q));
  if (!list.length) return `<p class="note">${esc(T().noLineResults(query))}</p>`;
  return `<ul class="list">${list
    .map(
      (l) => `<li><button type="button" data-action="line" data-code="${esc(l.code)}">
        <span class="chip">${esc(l.code)}</span><span class="label">${esc(l.name)}</span><span class="meta">${fmt(l.stops.length)}</span></button></li>`,
    )
    .join("")}</ul>`;
}

let lineQuery = "";
function viewLines() {
  const t = T();
  if (state.line) {
    const l = lineByCode.get(state.line);
    const len = lineKm(l);
    return `
      <button type="button" class="back" data-action="nav" data-href="#/lines">← ${esc(t.back)}</button>
      <div class="detail-head"><h2><span class="chip is-accent">${esc(l.code)}</span>${esc(l.name)}</h2></div>
      <dl class="stats">
        <div><dt>${esc(t.direction)}</dt><dd>${esc(l.direction)}</dd></div>
        <div><dt>${esc(t.tabs.stops)}</dt><dd>${fmt(l.stops.length)}</dd></div>
        <div><dt>${esc(t.length)}</dt><dd>${fmt(len.km, 1)} ${t.km}</dd></div>
      </dl>
      ${len.suspect ? `<p class="callout">${esc(t.suspectHops(len.suspect))}</p>` : ""}
      ${l.url ? `<p class="note"><a href="${esc(l.url)}" rel="noopener">${esc(t.moovit)}</a></p>` : ""}
      <section class="section">
        <h2>${esc(t.lineStops)}</h2>
        ${stopTimeline(l.stops, (id) => {
          const others = stopsById[id].lines.length - 1;
          return others > 0 ? `<span class="transfer">${esc(t.otherLines(others))}</span>` : "";
        })}
      </section>`;
  }
  return `
    <div class="search"><input type="search" data-input="lines" data-focus-key="lines-search" value="${esc(lineQuery)}" placeholder="${esc(t.searchLines)}" aria-label="${esc(t.searchLines)}"></div>
    <p class="note">${esc(t.linesCount(data.lines.length))}</p>
    <div data-results="lines">${lineItems(lineQuery)}</div>`;
}

function stopTimeline(ids, extra) {
  return `<ol class="stop-list">${ids
    .map((id) => {
      const s = stopsById[id];
      return `<li><button type="button" data-action="focus-stop" data-id="${id}"><span class="dot"></span>
        <span class="label">${esc(s.name)}${extra(id) || ""}</span><span class="meta">${s.quality === "approximate" ? '<span class="badge">≈</span>' : ""}</span></button></li>`;
    })
    .join("")}</ol>`;
}

let stopQuery = "";
function stopItems(query) {
  const t = T();
  const q = norm(query.trim());
  let list;
  if (!q) {
    list = [...data.stops].sort((a, b) => b.lines.length - a.lines.length || a.name.localeCompare(b.name)).slice(0, 15);
  } else {
    list = data.stops.filter((s) => norm(s.name).includes(q)).slice(0, 40);
    if (!list.length) return `<p class="note">${esc(t.noResults(query))}</p>`;
  }
  return `${q ? "" : `<h2>${esc(t.mostLines)}</h2>`}<ul class="list" style="margin-top:.5rem">${list
    .map(
      (s) => `<li><button type="button" data-action="stop" data-id="${s.id}">
        <span class="chip">${fmt(s.lines.length)}</span><span class="label">${esc(s.name)}</span><span class="meta">${s.quality === "approximate" ? '<span class="badge">≈</span>' : ""}</span></button></li>`,
    )
    .join("")}</ul>`;
}

function viewStops() {
  const t = T();
  if (state.stop != null) {
    const s = stopsById[state.stop];
    return `
      <button type="button" class="back" data-action="nav" data-href="#/stops">← ${esc(t.back)}</button>
      <div class="detail-head"><h2>${esc(s.name)}</h2>${s.address ? `<p class="note">${esc(s.address)}</p>` : ""}</div>
      ${s.quality === "approximate" ? `<p class="callout">${esc(t.stop.approximate)}</p>` : ""}
      <dl class="stats">
        <div><dt>${esc(t.stop.lines)}</dt><dd>${fmt(s.lines.length)}</dd></div>
        <div><dt>${esc(t.stop.centrality)}</dt><dd>${fmt(s.bt, 3)} <span class="note">(${esc(t.stop.rank(btRank[s.id], data.stops.length))})</span></dd></div>
        <div><dt>${esc(t.stop.inOut)}</dt><dd>${esc(t.stop.inOutValue(s.in, s.out))}</dd></div>
      </dl>
      <section class="section">
        <h2>${esc(t.stop.servedBy)}</h2>
        <div class="chips">${s.lines.map((c) => `<button type="button" class="chip" data-action="line" data-code="${esc(c)}">${esc(c)}</button>`).join("")}</div>
      </section>
      <div class="actions">
        <button type="button" class="btn" data-action="route-from" data-id="${s.id}">${esc(t.stop.routeFrom)}</button>
        <button type="button" class="btn" data-action="route-to" data-id="${s.id}">${esc(t.stop.routeTo)}</button>
      </div>`;
  }
  return `
    <div class="search"><input type="search" data-input="stops" data-focus-key="stops-search" value="${esc(stopQuery)}" placeholder="${esc(t.searchStops)}" aria-label="${esc(t.searchStops)}"></div>
    <div data-results="stops">${stopItems(stopQuery)}</div>`;
}

function combo(role) {
  const t = T().route;
  const id = state.route[role];
  const value = id != null ? stopsById[id].name : "";
  const picking = state.route.picking === role;
  return `<div class="combo">
    <label class="field-label" for="od-${role}">${esc(t[role])}</label>
    <div class="combo-row">
      <div class="search"><input id="od-${role}" type="text" autocomplete="off" role="combobox" aria-expanded="false" aria-autocomplete="list"
        data-input="od" data-role="${role}" data-focus-key="od-${role}" value="${esc(value)}" placeholder="${esc(t.placeholder)}"></div>
      <button type="button" class="pick${picking ? " is-active" : ""}" data-action="pick" data-role="${role}" aria-pressed="${picking}" title="${esc(t.pick)}" aria-label="${esc(t.pick)}">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>
      </button>
    </div>
    <div data-suggest="${role}"></div>
  </div>`;
}

function viewRoute() {
  const t = T().route;
  const r = state.route;
  let result = "";
  if (r.from != null && r.to != null) {
    if (r.from === r.to) result = `<p class="callout">${esc(t.same)}</p>`;
    else if (!r.result) result = `<p class="callout">${esc(t.noPath)}</p>`;
    else {
      const res = r.result;
      result = `
        <dl class="figures">
          <div><dt>${esc(t.distance)}</dt><dd>${fmt(res.km, 2)} ${T().km}</dd></div>
          <div><dt>${esc(t.stops)}</dt><dd>${fmt(res.stops.length)}</dd></div>
          <div><dt>${esc(t.transfers)}</dt><dd>${fmt(Math.max(res.rides.length - 1, 0))}</dd></div>
        </dl>
        ${r.isCase && data.route_case ? `<p class="note">${esc(t.caseNote(fmt(data.route_case.astar_km, 2)))}</p>` : ""}
        <section class="section">
          <h2>${esc(t.plan)}</h2>
          <div class="plan">${res.rides
            .map(([line, n], i) => `${i ? '<span class="arrow" aria-hidden="true">→</span>' : ""}<button type="button" class="chip is-accent" data-action="line" data-code="${esc(line)}">${esc(t.ride(line, n))}</button>`)
            .join("")}</div>
        </section>
        <section class="section">
          <h2>${esc(t.path)}</h2>
          ${stopTimeline(res.stops, (id) => {
            const i = res.stops.indexOf(id);
            return i > 0 ? `<span class="meta" style="display:block">${res.legLines[i - 1].map(esc).join(", ")}</span>` : "";
          })}
        </section>`;
    }
  }
  return `
    <p class="note">${esc(t.intro)}</p>
    <div class="od">
      <div class="fields">${combo("from")}${combo("to")}</div>
      <button type="button" class="swap" data-action="swap" title="${esc(t.swap)}" aria-label="${esc(t.swap)}">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3"/></svg>
      </button>
    </div>
    <div class="actions">
      ${data.route_case ? `<button type="button" class="btn${r.isCase ? " is-active" : ""}" data-action="route-case">${esc(t.case)}</button>` : ""}
      ${r.from != null || r.to != null ? `<button type="button" class="btn" data-action="route-clear">${esc(t.clear)}</button>` : ""}
    </div>
    <div class="stack" aria-live="polite">${result}</div>`;
}

function chartSvg() {
  const { c } = corridorView();
  const host = panel.querySelector("[data-chart]");
  const W = Math.max(260, host ? host.clientWidth : 340), H = 144, top = 8, bottom = 22, left = 34;
  const maxV = Math.max(1200, ...c.spacings_m) * 1.05;
  const y = (v) => top + (H - top - bottom) * (1 - v / maxV);
  const n = c.spacings_m.length;
  const bw = (W - left) / n;
  const bars = c.spacings_m
    .map((d, i) => {
      const x = left + i * bw + 1.5;
      const h = H - bottom - y(d);
      const a = c.points[i], b = c.points[i + 1];
      const label = `${T().corridor.between(a.stops[0], b.stops[0])}: ${fmt(d)} ${T().m}`;
      return `<rect class="bar" data-i="${i}" x="${x.toFixed(1)}" y="${y(d).toFixed(1)}" width="${Math.max(2, bw - 3).toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" rx="2" fill="${classOf(d).color}"><title>${esc(label)}</title></rect>`;
    })
    .join("");
  const ticks = [0, 400, 800, 1200].map((v) => `<text class="axis" x="${left - 6}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end">${fmt(v)}</text>`).join("");
  const ty = y(state.corridorMax).toFixed(1);
  return `<svg class="spacing-chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(T().corridor.chart)}">
    ${ticks}<line class="baseline" x1="${left}" x2="${W}" y1="${H - bottom}" y2="${H - bottom}"/>
    ${bars}
    <line class="threshold" data-threshold x1="${left}" x2="${W}" y1="${ty}" y2="${ty}"/>
    <text class="axis" x="${left}" y="${H - 6}">${esc(c.points[0].stops[0].slice(0, 28))}</text>
    <text class="axis" x="${W}" y="${H - 6}" text-anchor="end">${esc(c.points[c.points.length - 1].stops[0].slice(0, 28))}</text>
  </svg>`;
}

function gapList() {
  const t = T().corridor;
  const { gaps } = corridorView();
  return `<p><strong>${esc(t.gaps(gaps.length))}</strong></p>
    <ul class="list" style="margin-top:.4rem">${gaps
      .map(
        (g) => `<li><button type="button" data-action="gap" data-i="${g.i}">
          <span class="chip" style="color:${classOf(g.d).color}">${fmt(g.d)} ${T().m}</span>
          <span class="label">${esc(t.between(g.a.stops[0], g.b.stops[0]))}${g.a.approximate || g.b.approximate ? ` <span class="badge">≈</span>` : ""}</span><span></span></button></li>`,
      )
      .join("")}</ul>`;
}

function viewCorridor() {
  const t = T().corridor;
  const c = data.corridor;
  const km = c.spacings_m.reduce((a, b) => a + b, 0) / 1000;
  const sorted = [...c.spacings_m].sort((a, b) => a - b);
  const mid = sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
  return `
    <section class="section">
      <h2>${esc(t.title)}</h2>
      <p class="note">${esc(t.intro(fmt(c.points.length), fmt(km, 2), fmt(mid)))}</p>
    </section>
    <section class="section">
      <div class="range-row"><label class="field-label" for="gap">${esc(t.threshold)}</label><span class="range-value" data-range-value>${fmt(state.corridorMax)} ${T().m}</span></div>
      <input id="gap" class="range" type="range" min="250" max="1000" step="25" value="${state.corridorMax}" data-input="gap">
    </section>
    <section class="section">
      <h2 class="field-label">${esc(t.chart)}</h2>
      <div data-chart>${""}</div>
      <div class="class-legend">${c.classes.map(([, colour], i) => `<span><i class="swatch" style="background:${CLASS_COLORS[colour]}"></i>${esc(t.classes[i])}</span>`).join("")}</div>
    </section>
    <section class="section">
      <div data-gaps>${gapList()}</div>
      <p class="note" style="margin-top:.5rem">${esc(t.gapsNote)}</p>
    </section>`;
}

const VIEWS = { overview: viewOverview, lines: viewLines, stops: viewStops, route: viewRoute, corridor: viewCorridor };

let lastViewKey = "";
function render({ fit = true } = {}) {
  const key = `${state.tab}|${state.line}|${state.stop}`;
  const focusKey = document.activeElement?.dataset?.focusKey;
  const scroll = key === lastViewKey ? panel.scrollTop : 0;
  panel.innerHTML = VIEWS[state.tab]();
  if (state.tab === "corridor") panel.querySelector("[data-chart]").innerHTML = chartSvg();
  panel.scrollTop = scroll;
  if (key !== lastViewKey && !reduceMotion) {
    panel.classList.remove("view-enter");
    void panel.offsetWidth;
    panel.classList.add("view-enter");
  }
  lastViewKey = key;
  if (focusKey) {
    const el = panel.querySelector(`[data-focus-key="${focusKey}"]`);
    if (el) {
      el.focus({ preventScroll: true });
      if (el.setSelectionRange && el.type !== "range") el.setSelectionRange(el.value.length, el.value.length);
    }
  }
  updateTabs();
  pickBanner.hidden = !state.route.picking;
  pickBanner.textContent = T().route.picking;
  syncMap({ fit });
}

function updateTabs() {
  const tabs = [...document.querySelectorAll("[data-tab]")];
  for (const tab of tabs) {
    const on = tab.dataset.tab === state.tab;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
  }
  const active = tabs.find((t) => t.dataset.tab === state.tab);
  const ind = document.querySelector(".tab-indicator");
  if (active && ind) ind.style.transform = `translateX(${active.offsetLeft + 8}px) scaleX(${Math.max(1, active.offsetWidth - 16)})`;
}

function applyStaticText() {
  const t = T();
  document.documentElement.lang = state.lang === "pt" ? "pt-BR" : "en";
  document.title = `${t.title} · ${state.lang === "pt" ? "Maricá bus network" : "Rede de ônibus de Maricá"}`;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const v = lookup(el.dataset.i18n);
    if (typeof v === "string") el.textContent = v;
  });
  document.querySelectorAll("[data-i18n-label]").forEach((el) => {
    const v = lookup(el.dataset.i18nLabel);
    if (typeof v === "string") el.setAttribute("aria-label", v);
  });
  document.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === state.lang)));
}

/* ---------------------------------------------------------------- routing */

function go(hash) {
  if (location.hash === hash) applyHash();
  else location.hash = hash;
}
function goRoute() {
  const r = state.route;
  go(r.isCase ? "#/route/case" : `#/route/${r.from ?? "-"}/${r.to ?? "-"}`);
}

function applyHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [tab = "overview", a, b] = parts;
  const prevStop = state.tab === "stops" ? state.stop : null;
  state.tab = VIEWS[tab] ? tab : "overview";
  state.line = null;
  state.stop = null;
  state.focus = null;
  if (state.tab !== "route") state.route.picking = null;

  if (state.tab === "overview") state.region = a === "itaipuacu" ? "itaipuacu" : "marica";
  if (state.tab === "lines" && a && lineByCode.has(decodeURIComponent(a))) state.line = decodeURIComponent(a);
  if (state.tab === "stops" && a != null && stopsById[Number(a)]) state.stop = Number(a);
  if (state.tab === "corridor") state.region = "itaipuacu";
  if (state.tab === "route") {
    const r = state.route;
    if (a === "case" && data.route_case) {
      r.isCase = true;
      r.from = data.route_case.stops[0];
      r.to = data.route_case.stops[data.route_case.stops.length - 1];
      r.result = describePath(data.route_case.stops);
    } else {
      r.isCase = false;
      r.from = a != null && a !== "-" && stopsById[Number(a)] ? Number(a) : null;
      r.to = b != null && b !== "-" && stopsById[Number(b)] ? Number(b) : null;
      r.result = r.from != null && r.to != null && r.from !== r.to ? shortestPath(r.from, r.to) : null;
    }
  }
  render();
  if (state.tab === "stops" && state.stop != null && state.stop !== prevStop) setTimeout(pulseSelection, reduceMotion ? 0 : 500);
}

/* ---------------------------------------------------------------- events */

document.querySelector(".lang").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-lang]");
  if (!btn || btn.dataset.lang === state.lang) return;
  state.lang = btn.dataset.lang;
  try {
    localStorage.setItem("marica-lang", state.lang);
  } catch {}
  applyStaticText();
  render({ fit: false });
});

document.querySelector(".tabs").addEventListener("click", (e) => {
  const tab = e.target.closest("[data-tab]");
  if (!tab) return;
  const t = tab.dataset.tab;
  if (t === "overview") go(`#/overview${state.region === "itaipuacu" ? "/itaipuacu" : ""}`);
  else if (t === "route") goRoute();
  else go(`#/${t}`);
});
document.querySelector(".tabs").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const tabs = [...document.querySelectorAll("[data-tab]")];
  const i = tabs.findIndex((t) => t.dataset.tab === state.tab);
  const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
  next.click();
  next.focus();
});

panel.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const { action } = el.dataset;
  const id = el.dataset.id != null ? Number(el.dataset.id) : null;
  switch (action) {
    case "nav":
      return go(el.dataset.href);
    case "region":
      return go(`#/overview${el.dataset.value === "itaipuacu" ? "/itaipuacu" : ""}`);
    case "mode":
      state.mode = el.dataset.value;
      return render({ fit: false });
    case "stop":
      return go(`#/stops/${id}`);
    case "line":
      return go(`#/lines/${encodeURIComponent(el.dataset.code)}`);
    case "focus-stop":
      state.focus = id;
      syncMap({ fit: false });
      flyToStop(id, 15);
      return setTimeout(pulseSelection, reduceMotion ? 0 : 400);
    case "route-from":
    case "route-to":
      state.route[action === "route-from" ? "from" : "to"] = id;
      state.route.isCase = false;
      return goRoute();
    case "route-case":
      return go("#/route/case");
    case "route-clear":
      Object.assign(state.route, { from: null, to: null, isCase: false, result: null, picking: null });
      return goRoute();
    case "swap": {
      el.classList.toggle("spin");
      const r = state.route;
      [r.from, r.to] = [r.to, r.from];
      r.isCase = false;
      return setTimeout(goRoute, reduceMotion ? 0 : 150);
    }
    case "pick":
      state.route.picking = state.route.picking === el.dataset.role ? null : el.dataset.role;
      return render({ fit: false });
    case "suggest":
      state.route[el.dataset.role] = id;
      state.route.isCase = false;
      return goRoute();
    case "gap": {
      const i = Number(el.dataset.i);
      const c = data.corridor;
      fitCoords([[c.points[i].lon, c.points[i].lat], [c.points[i + 1].lon, c.points[i + 1].lat]], 16);
      return hoverSegment(i);
    }
  }
});

panel.addEventListener("input", (e) => {
  const el = e.target;
  if (el.dataset.input === "lines") {
    lineQuery = el.value;
    panel.querySelector('[data-results="lines"]').innerHTML = lineItems(lineQuery);
  } else if (el.dataset.input === "stops") {
    stopQuery = el.value;
    panel.querySelector('[data-results="stops"]').innerHTML = stopItems(stopQuery);
  } else if (el.dataset.input === "gap") {
    state.corridorMax = Number(el.value);
    panel.querySelector("[data-range-value]").textContent = `${fmt(state.corridorMax)} ${T().m}`;
    panel.querySelector("[data-gaps]").innerHTML = gapList();
    panel.querySelector("[data-chart]").innerHTML = chartSvg();
    syncCorridor();
  } else if (el.dataset.input === "od") {
    suggest(el);
  }
});

function suggest(input) {
  const role = input.dataset.role;
  const box = panel.querySelector(`[data-suggest="${role}"]`);
  const q = norm(input.value.trim());
  if (q.length < 2) {
    box.innerHTML = "";
    input.setAttribute("aria-expanded", "false");
    return;
  }
  const hits = data.stops.filter((s) => norm(s.name).includes(q)).slice(0, 12);
  input.setAttribute("aria-expanded", String(hits.length > 0));
  box.innerHTML = hits.length
    ? `<div class="suggestions" role="listbox">${hits
        .map((s, i) => `<button type="button" role="option" aria-selected="${i === 0}" data-action="suggest" data-role="${role}" data-id="${s.id}">${esc(s.name)}</button>`)
        .join("")}</div>`
    : "";
}

panel.addEventListener("keydown", (e) => {
  const el = e.target;
  if (el.dataset.input !== "od") return;
  const box = panel.querySelector(`[data-suggest="${el.dataset.role}"] .suggestions`);
  if (!box) return;
  const opts = [...box.querySelectorAll("[role=option]")];
  let i = opts.findIndex((o) => o.getAttribute("aria-selected") === "true");
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    i = (i + (e.key === "ArrowDown" ? 1 : opts.length - 1)) % opts.length;
    opts.forEach((o, j) => o.setAttribute("aria-selected", String(j === i)));
    opts[i].scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter" && opts[i]) {
    e.preventDefault();
    opts[i].click();
  } else if (e.key === "Escape") {
    box.remove();
    el.setAttribute("aria-expanded", "false");
  }
});

panel.addEventListener("mouseover", (e) => {
  const bar = e.target.closest(".spacing-chart .bar");
  if (bar) hoverSegment(Number(bar.dataset.i));
});
panel.addEventListener("mouseout", (e) => {
  if (e.target.closest(".spacing-chart .bar")) hoverSegment(null);
});
panel.addEventListener("click", (e) => {
  const bar = e.target.closest(".spacing-chart .bar");
  if (!bar) return;
  const i = Number(bar.dataset.i), c = data.corridor;
  fitCoords([[c.points[i].lon, c.points[i].lat], [c.points[i + 1].lon, c.points[i + 1].lat]], 16);
});

let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    updateTabs();
    if (state.tab === "corridor") panel.querySelector("[data-chart]").innerHTML = chartSvg();
  }, 120);
});
window.addEventListener("hashchange", applyHash);
darkQuery.addEventListener("change", () => {
  if (!map) return;
  map.setStyle(darkQuery.matches ? STYLES.dark : STYLES.light);
});

/* ---------------------------------------------------------------- boot */

async function boot() {
  applyStaticText();
  const res = await fetch("data/network.json");
  data = await res.json();
  index();
  map = new maplibregl.Map({
    container: "map",
    style: darkQuery.matches ? STYLES.dark : STYLES.light,
    bounds: data.view.marica,
    fitBoundsOptions: { padding: 40 },
    attributionControl: { compact: true },
    dragRotate: false,
    pitchWithRotate: false,
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.FullscreenControl(), "top-right");
  map.on("style.load", () => {
    addLayers();
    if (!booted) {
      booted = true;
      applyHash();
    }
  });
}
let booted = false;
boot().catch((err) => {
  panel.innerHTML = `<p class="callout">Unable to load the network data. ${esc(err.message)}</p>`;
  console.error(err);
});
