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

const charts = [];
const elements = new Map();

function makeElement(id) {
  return {
    id,
    textContent: "",
    innerHTML: "",
    hidden: false,
    dataset: {},
    style: {},
    addEventListener() {},
    classList: { add() {}, remove() {}, toggle() {} },
  };
}

global.document = {
  getElementById(id) {
    if (id === "report-data") return { textContent: payload };
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

function Chart(element, config) {
  charts.push({ element, config });
}
Chart.defaults = { font: {}, plugins: {}, animation: true };
global.Chart = Chart;

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
check("title is populated", /2024/.test(text("report-title")), text("report-title"));
check("subtitle names the sport and device",
  /Canoeing/.test(text("report-subtitle")) && /Wahoo/.test(text("report-subtitle")),
  text("report-subtitle"));

check("stat grid rendered", html("stat-grid").includes("Distance") && html("stat-grid").includes("Max speed"));
check("stat grid shows a distance in km", /km/.test(html("stat-grid")));
check("stat grid shows avg and max heart rate", /Avg HR/.test(html("stat-grid")) && /Max HR/.test(html("stat-grid")));
check("stat count is plausible",
  (html("stat-grid").match(/class="stat/g) || []).length >= 12,
  (html("stat-grid").match(/class="stat/g) || []).length + " stats");

check("histogram note has distribution stats", /mean/.test(text("hist-note")) && /km\/h/.test(text("hist-note")), text("hist-note"));
check("rolling-average legend labelled", /rolling average/.test(text("rolling-legend")), text("rolling-legend"));

check("four charts created", charts.length === 4, String(charts.length));

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
check("speed trace is in km/h and never negative",
  speedValues.length > 0 && speedValues.every((y) => y >= 0 && y < 40),
  "min=" + Math.min(...speedValues) + " max=" + Math.max(...speedValues));
check("speed trace x-axis is elapsed seconds",
  !!speedTrace && speedTrace.config.data.datasets[0].data.every((p) => p.x >= 0 && p.x < 5000),
  speedTrace ? "xmax=" + Math.max(...speedTrace.config.data.datasets[0].data.map((p) => p.x)) : "n/a");

check("heart-rate card revealed", (elements.get("hr-card") || {}).hidden === false);
check("elevation card revealed", (elements.get("elev-card") || {}).hidden === false);
check("splits card revealed", (elements.get("splits-card") || {}).hidden === false);
check("track card revealed", (elements.get("track-card") || {}).hidden === false);

const splitRows = (html("splits-body").match(/<tr>/g) || []).length;
check("splits table has rows", splitRows === 4, splitRows + " rows");
check("splits table shows pace", /\/km/.test(html("splits-body")));

check("track path generated", /<path d="M/.test(html("track")));
check("track has start and finish markers", (html("track").match(/<circle/g) || []).length === 2);
check("track note reports point count", /GPS points/.test(text("track-note")), text("track-note"));

check("file note reports sample counts", /samples recorded/.test(text("file-note")), text("file-note"));

console.log("\n" + (failures.length ? "FAILURES: " + failures.length : "ALL CHECKS PASSED"));
process.exit(failures.length ? 1 : 0);
