/*
 * Executes app/static/js/report.js against the real embedded payload using a
 * minimal DOM stub, so the report rendering can be verified without a browser.
 *
 * Usage: node tools/verify_report_js.js <payload.json> <report.js>
 */
"use strict";

const fs = require("fs");

const payload = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");

// Optional 4th argument: the basemap catalogue JSON as the server renders it.
// Falling back keeps the harness runnable with two arguments.
const basemapSource = process.argv[4]
  ? fs.readFileSync(process.argv[4], "utf8").trim()
  : JSON.stringify([
      {
        key: "streets",
        label: "Streets",
        url: "https://tile.example/{z}/{x}/{y}.png",
        attribution: "test",
        max_zoom: 19,
        subdomains: "abc",
      },
      {
        key: "satellite",
        label: "Satellite",
        url: "https://sat.example/{z}/{y}/{x}",
        attribution: "test",
        max_zoom: 19,
        subdomains: "abc",
      },
    ]);

const data = JSON.parse(payload);
const charts = [];
const elements = new Map();

// Declared up front: the checks below reference these before the rolling-window
// section would otherwise have defined them.
const trace = data.trace || {};
const options = trace.rolling_options || [];
const windows = trace.rolling_windows || {};
const pointCount = (trace.t || []).length;

// Cards and containers the template ships hidden. The stub must start in the
// same state or "was it revealed?" checks test nothing.
const INITIALLY_HIDDEN = new Set([
  "map",
  "track",
  "hr-card",
  "elev-card",
  "splits-card",
  "track-card",
]);

// Ordered log of visibility changes and map construction, so the harness can
// assert *when* things happened, not just that they happened. A map handed a
// display:none container initialises "successfully" and then never loads a
// tile, which no state-only check can see.
const events = [];

function makeElement(id) {
  let hidden = INITIALLY_HIDDEN.has(id);
  return {
    id,
    textContent: "",
    innerHTML: "",
    get hidden() { return hidden; },
    set hidden(value) {
      if (hidden !== value) events.push((value ? "hide:" : "show:") + id);
      hidden = value;
    },
    value: "",
    dataset: {},
    style: {},
    _listeners: {},
    addEventListener(type, fn) {
      (this._listeners[type] = this._listeners[type] || []).push(fn);
    },
    setAttribute() {},
    querySelectorAll() {
      return [];
    },
    classList: { add() {}, remove() {}, toggle() {} },
  };
}

global.document = {
  getElementById(id) {
    if (id === "report-data") return { textContent: payload };
    if (id === "basemap-data") return { textContent: basemapSource };
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  querySelectorAll() {
    return [];
  },
  addEventListener() {},
};

global.window = { confirm: () => false, alert: () => {}, location: {} };
global.fetch = () => Promise.resolve({ ok: true });
// Run frame callbacks immediately: the harness is single-threaded and just
// needs the deferred work to happen.
global.requestAnimationFrame = (fn) => { fn(); return 1; };

function Chart(element, config) {
  // Mirror enough of the real Chart.js instance that report.js can hold a
  // reference, swap a dataset and call update().
  this.element = element;
  this.config = config;
  this.data = config.data;
  this.options = config.options;
  this.updates = 0;
  this.update = function () { this.updates += 1; };
  charts.push(this);
}
Chart.defaults = { font: {}, plugins: {}, animation: true };
global.Chart = Chart;

// Leaflet stub. Set NO_LEAFLET=1 to exercise the offline SVG fallback instead.
const withLeaflet = !process.env.NO_LEAFLET;
const leaflet = {
  calls: { container: null, tiles: [], removed: [], polylines: [], markers: [], tileEvents: [], fitBounds: 0, invalidateSize: 0 },
};

if (withLeaflet) {
  const mapObject = {
    removeLayer(layer) { leaflet.calls.removed.push(layer.url); },
    fitBounds(bounds) { leaflet.calls.fitBounds += 1; leaflet.calls.boundsPoints = bounds.n; },
    invalidateSize() { leaflet.calls.invalidateSize += 1; },
  };
  global.L = {
    map(container) {
      // Recorded so the ordering checks can prove the container was visible.
      events.push("map:init");
      leaflet.calls.container = container.id;
      return mapObject;
    },
    tileLayer(url, options) {
      return {
        url,
        options,
        on(type, handler) { leaflet.calls.tileEvents.push(type); return this; },
        addTo() { leaflet.calls.tiles.push(url); return this; },
      };
    },
    polyline(points, options) {
      leaflet.calls.polylines.push({ points: points.length, options });
      return { addTo() { return this; } };
    },
    circleMarker(point, options) {
      leaflet.calls.markers.push({ point, options });
      return { bindTooltip() { return this; }, addTo() { return this; } };
    },
    latLngBounds(points) { return { n: points.length }; },
  };
}

// Run the report IIFE exactly as a browser would.
eval(reportSource);

/* ------------------------------- assertions ------------------------------ */

const failures = [];
function check(name, condition, detail) {
  if (condition) {
    console.log("  PASS  " + name);
  } else {
    failures.push(name + (detail ? " (" + detail + ")" : ""));
    console.log("  FAIL  " + name + (detail ? "  -> " + detail : ""));
  }
}

const text = (id) => (elements.get(id) || {}).textContent || "";
const html = (id) => (elements.get(id) || {}).innerHTML || "";
// Mirrors document.getElementById: creates the node on first touch, seeded with
// the hidden state the template ships it in. Without this, a card report.js
// never revealed reads as undefined instead of "still hidden".
const element = (id) => document.getElementById(id);
const click = (id) => {
  const node = element(id);
  if (!node || !node._listeners || !node._listeners.click) return false;
  node._listeners.click.forEach((fn) => fn());
  return true;
};

console.log("chart instances:", charts.length);
charts.forEach((c, i) => {
  const datasets = (c.config.data && c.config.data.datasets) || [];
  console.log(
    "  [" + i + "] " + c.config.type +
    " datasets=" + datasets.length +
    " points=" + datasets.map((d) => d.data.length).join("/") +
    " canvas=" + (c.element && c.element.id)
  );
});

console.log("\nchecks:");
// The report title is rendered server-side in the configured display timezone,
// so this harness has nothing to assert about it: it only executes report.js,
// which deliberately does not rebuild that text. What matters here is that the
// payload still carries the timestamp the server rendered from.
check("payload carries a start time for the server-rendered title",
  typeof data.meta.start_time === "string" && data.meta.start_time.length > 0,
  String(data.meta.start_time));
check("subtitle names the sport and device",
  text("report-subtitle").length > 0
  && text("report-subtitle").toLowerCase()
       .includes(String(data.meta.sport || "").replace(/_/g, " ").toLowerCase()),
  text("report-subtitle"));

check("stat grid rendered", html("stat-grid").includes("Distance") && html("stat-grid").includes("Max speed"));
check("stat grid shows a distance in km", /km/.test(html("stat-grid")));
check("stat grid heart-rate stats follow the data",
  data.summary.has_hr
    ? (/Avg HR/.test(html("stat-grid")) && /Max HR/.test(html("stat-grid")))
    : !/Avg HR/.test(html("stat-grid")),
  "has_hr=" + data.summary.has_hr);
check("stat count is plausible",
  (html("stat-grid").match(/class="stat/g) || []).length >= 9,
  (html("stat-grid").match(/class="stat/g) || []).length + " stats");

check("histogram note has distribution stats", /mean/.test(text("hist-note")) && /km\/h/.test(text("hist-note")), text("hist-note"));
check("rolling-average legend labelled", /rolling average/.test(text("rolling-legend")), text("rolling-legend"));

const expectedCharts =
  1 +                                   // speed histogram
  1 +                                   // speed trace
  (data.summary.has_hr ? 1 : 0) +       // heart rate
  (data.show_elevation ? 1 : 0);        // elevation
check("chart count matches the data", charts.length === expectedCharts,
  charts.length + " vs expected " + expectedCharts);

const histogram = charts.find((c) => c.config.type === "bar");
check("histogram is a bar chart", !!histogram);
check("histogram has 24 bins", !!histogram && histogram.config.data.labels.length === 24,
  histogram ? String(histogram.config.data.labels.length) : "n/a");

const speedTrace = charts.find(
  (c) => c.config.type === "line" && c.element && c.element.id === "speed-trace"
);
check("speed trace exists", !!speedTrace);
check("speed trace has raw + rolling datasets", !!speedTrace && speedTrace.config.data.datasets.length === 2);

const speedValues = speedTrace
  ? speedTrace.config.data.datasets.flatMap((d) => d.data.map((p) => p.y))
  : [];
const maxKmh = data.summary.max_speed_ms * 3.6;
check("speed trace is in km/h and never negative",
  speedValues.length > 0 && speedValues.every((y) => y >= 0 && y <= maxKmh + 0.5),
  "min=" + Math.min(...speedValues) + " max=" + Math.max(...speedValues) + " vs maxKmh=" + maxKmh.toFixed(1));

const lastT = trace.t[trace.t.length - 1];
check("speed trace x-axis is elapsed seconds",
  !!speedTrace && speedTrace.config.data.datasets[0].data.every((p) => p.x >= 0 && p.x <= lastT + 1),
  speedTrace ? "xmax=" + Math.max(...speedTrace.config.data.datasets[0].data.map((p) => p.x)) + " session=" + lastT : "n/a");

check("heart-rate card follows the data",
  element("hr-card").hidden === !data.summary.has_hr,
  "hidden=" + element("hr-card").hidden + " has_hr=" + data.summary.has_hr);
check("elevation card obeys the water rule",
  element("elev-card").hidden === !data.show_elevation,
  "hidden=" + element("elev-card").hidden + " show_elevation=" + data.show_elevation);
check("ascent/descent stats obeys the water rule",
  /Ascent/.test(html("stat-grid")) === data.show_elevation,
  "show_elevation=" + data.show_elevation);
check("splits card revealed", element("splits-card").hidden === false);
check("track card revealed", element("track-card").hidden === false);

const expectedSplits = (data.splits || []).length;
const splitRows = (html("splits-body").match(/<tr>/g) || []).length;
check("splits table has a row per split", splitRows === expectedSplits,
  splitRows + " rows vs " + expectedSplits + " splits");
check("splits table shows pace", /\/km/.test(html("splits-body")));

/* ------------------------- route map / basemaps -------------------------- */

console.log("\nroute map (" + (withLeaflet ? "leaflet" : "offline SVG fallback") + "):");
console.log("  visibility / init order: " + events.join(" -> "));

const catalogue = JSON.parse(basemapSource);
const basemapKeys = catalogue.map((b) => b.key);

check("a basemap button exists per provider",
  basemapKeys.length > 0 && basemapKeys.every((k) => !!element("basemap-" + k)),
  basemapKeys.join(", "));

if (withLeaflet) {
  // Ordering matters: Leaflet must be handed a laid-out container, or it
  // measures 0x0 and silently never requests a tile.
  const initAt = events.indexOf("map:init");
  const cardShownAt = events.indexOf("show:track-card");
  const mapShownAt = events.indexOf("show:map");

  check("the route card is visible before Leaflet measures the container",
    initAt !== -1 && cardShownAt !== -1 && cardShownAt < initAt,
    events.join(" -> "));
  check("the map container is visible before Leaflet measures it",
    initAt !== -1 && mapShownAt !== -1 && mapShownAt < initAt,
    events.join(" -> "));
  check("tile failures are watched for diagnostics",
    leaflet.calls.tileEvents.filter((t) => t === "tileerror").length >= 1,
    leaflet.calls.tileEvents.join(", ") || "no tile events");

  check("map attached to the route container",
    leaflet.calls.container === "map", String(leaflet.calls.container));
  check("map container revealed", element("map").hidden === false);
  check("opening basemap tiles added",
    leaflet.calls.tiles.length === 1 && leaflet.calls.tiles[0] === catalogue[0].url,
    leaflet.calls.tiles.join(" "));
  check("route polyline carries every point",
    leaflet.calls.polylines.length === 1
    && leaflet.calls.polylines[0].points === data.track.length,
    JSON.stringify(leaflet.calls.polylines.map((p) => p.points)) + " vs " + data.track.length);
  check("start and finish markers added",
    leaflet.calls.markers.length === 2, String(leaflet.calls.markers.length));
  check("map fitted to the route bounds",
    leaflet.calls.fitBounds === 1 && leaflet.calls.boundsPoints === data.track.length,
    leaflet.calls.fitBounds + " fits / " + leaflet.calls.boundsPoints + " points");
  check("map size invalidated after reveal",
    leaflet.calls.invalidateSize === 1, String(leaflet.calls.invalidateSize));
  check("basemap buttons all wired",
    basemapKeys.every((k) => !!element("basemap-" + k)._listeners.click));
  check("offline SVG fallback not rendered", element("track").hidden === true);

  const second = basemapKeys[1];
  check("switching basemap is handled", click("basemap-" + second));
  check("old tiles removed and new tiles added",
    leaflet.calls.removed.length === 1
    && leaflet.calls.tiles.length === 2
    && leaflet.calls.tiles[1] === catalogue[1].url,
    "removed=" + leaflet.calls.removed.length + " added=" + leaflet.calls.tiles.length);
} else {
  check("offline SVG route path rendered", /<path d="M/.test(html("track")), html("track").slice(0, 32));
  check("offline SVG has start and finish markers",
    (html("track").match(/<circle/g) || []).length === 2);
  check("basemap control hidden without leaflet",
    element("basemap-control").hidden === true);
  check("map container left hidden", element("map").hidden === true);
}

check("track note reports point count", /GPS points/.test(text("track-note")), text("track-note"));
check("route note adapts to the map being used",
  withLeaflet ? /change the background/.test(text("track-note"))
              : /plotted/.test(text("track-note")),
  text("track-note"));

check("file note reports sample counts", /samples recorded/.test(text("file-note")), text("file-note"));

/* ------------------- selectable rolling-average window ------------------- */

console.log("\nrolling-average window picker:");

check("window options offered", options.length >= 2, options.join(", "));
check("spans 30 s to 10 min",
  Math.min(...options) === 30 && Math.max(...options) === 600, options.join(", "));
check("offers five choices", options.length === 5, options.join(", "));
check("a rolling series is embedded per option",
  options.every((w) => Array.isArray(windows[String(w)]) && windows[String(w)].length === pointCount),
  options.map((w) => w + ":" + (windows[String(w)] || []).length).join(" "));
check("picker has a button per option",
  options.every((w) => !!element("window-" + w)), options.join(", "));
check("picker buttons have click handlers",
  options.every((w) => !!element("window-" + w)._listeners.click),
  "at least one button is unwired");
check("opens on the 30 s window",
  /30 s rolling average/.test(text("rolling-legend")), text("rolling-legend"));

function variance(points) {
  const ys = points.map((p) => p.y);
  const mean = ys.reduce((a, b) => a + b, 0) / ys.length;
  return ys.reduce((a, b) => a + (b - mean) * (b - mean), 0) / ys.length;
}

const asKmh = (values) =>
  values.map((v, i) => ({ x: trace.t[i], y: v === null ? null : v * 3.6 }))
        .filter((p) => p.y !== null);

const shortest = Math.min(...options);
const longest = Math.max(...options);

// Selecting the longest window must swap the chart's rolling dataset.
const before = speedTrace.config.data.datasets[1].data;
const varShort = variance(before);
check("opens with the shortest window's series",
  before.length === asKmh(windows[String(shortest)]).length,
  before.length + " vs " + asKmh(windows[String(shortest)]).length);

check("clicking a window is handled", click("window-" + longest));
const after = speedTrace.config.data.datasets[1].data;
const varLong = variance(after);

check("legend reflects the new window",
  /10 min rolling average/.test(text("rolling-legend")), text("rolling-legend"));
check("rolling dataset was replaced",
  after !== before && after.length === before.length,
  after.length + " points");
check("chart was re-rendered on switch", speedTrace.updates > 0, speedTrace.updates + " updates");
check("longer window is visibly smoother", varLong < varShort,
  "variance 30s=" + varShort.toFixed(4) + " 600s=" + varLong.toFixed(4));

click("window-" + shortest);
check("switching back restores the short window",
  variance(speedTrace.config.data.datasets[1].data) > varLong
  && /30 s rolling average/.test(text("rolling-legend")),
  text("rolling-legend"));

console.log("\nvariance by window (smoother = lower):");
options.forEach((w) => {
  console.log("  " + String(w).padStart(3) + " s  " + variance(asKmh(windows[String(w)])).toFixed(4));
});

console.log("\n" + (failures.length ? "FAILURES: " + failures.length : "ALL CHECKS PASSED"));
process.exit(failures.length ? 1 : 0);
