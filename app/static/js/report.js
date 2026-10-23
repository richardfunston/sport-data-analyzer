/* Report page: renders the embedded report payload into stats and charts. */
(function () {
  "use strict";

  var node = document.getElementById("report-data");
  if (!node) return;

  var report;
  try {
    report = JSON.parse(node.textContent);
  } catch (err) {
    document.getElementById("report-subtitle").textContent = "Could not read the report payload.";
    return;
  }

  var meta = report.meta || {};
  var summary = report.summary || {};
  var histogram = report.histogram;
  var trace = report.trace || {};
  var splits = report.splits || [];
  var track = report.track || [];
  // Withheld on water: the altitude trace there is surface noise, not terrain.
  var showElevation = report.show_elevation !== false;

  var hasChart = typeof Chart !== "undefined";
  if (hasChart) {
    Chart.defaults.color = "#8ba0b3";
    Chart.defaults.borderColor = "rgba(34, 48, 63, 0.9)";
    Chart.defaults.font.family =
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
    Chart.defaults.font.size = 11;
    Chart.defaults.animation = false;
  }

  /* ---------------- formatting ---------------- */

  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function hms(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    var total = Math.round(seconds);
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    return h ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
  }

  function km(metres) {
    return metres === null || metres === undefined ? "—" : (metres / 1000).toFixed(2) + " km";
  }

  function kmh(ms) {
    return ms === null || ms === undefined ? "—" : (ms * 3.6).toFixed(1) + " km/h";
  }

  function pace(ms) {
    if (!ms || ms <= 0.05) return "—";
    var perKm = 1000 / ms;
    return Math.floor(perKm / 60) + ":" + pad(Math.round(perKm % 60)) + " /km";
  }

  /* ---------------- header ---------------- */

  /* The title is rendered server-side in the configured display zone. It is
     deliberately not rebuilt here: doing it in the browser made the report
     header show the viewer's zone while the index showed Sydney. */

  var sport = meta.sub_sport && meta.sub_sport !== "generic" ? meta.sub_sport : meta.sport;

  var subtitleBits = [];
  if (sport) subtitleBits.push(sport.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); }));
  if (meta.device) subtitleBits.push(meta.device);
  if (meta.sample_count) subtitleBits.push(meta.sample_count.toLocaleString() + " samples");
  document.getElementById("report-subtitle").textContent = subtitleBits.join(" · ");

  /* ---------------- stat grid ---------------- */

  var grid = document.getElementById("stat-grid");
  var stats = [
    ["Distance", km(summary.distance_m), null, "accent"],
    ["Moving time", hms(summary.moving_s), null, null],
    ["Elapsed", hms(summary.elapsed_s), null, null],
    ["Avg speed", kmh(summary.avg_moving_speed_ms), null, "accent"],
    ["Avg speed (total)", kmh(summary.avg_speed_ms), null, null],
    ["Max speed", kmh(summary.max_speed_ms), null, "warm"],
    ["Avg pace", pace(summary.avg_moving_speed_ms), null, null],
    ["Best pace", pace(summary.max_speed_ms), null, null],
    ["Stopped", hms(summary.stopped_s), null, null],
  ];

  if (summary.has_hr) {
    stats.push(["Avg HR", summary.avg_hr === null ? "—" : Math.round(summary.avg_hr), "bpm", null]);
    stats.push(["Max HR", summary.max_hr === null ? "—" : Math.round(summary.max_hr), "bpm", "warm"]);
  }
  if (summary.has_cadence) {
    stats.push(["Avg cadence", summary.avg_cadence === null ? "—" : Math.round(summary.avg_cadence), "spm", null]);
    stats.push(["Max cadence", summary.max_cadence === null ? "—" : Math.round(summary.max_cadence), "spm", null]);
  }
  if (showElevation && summary.ascent_m !== null && summary.ascent_m !== undefined) {
    stats.push(["Ascent", Math.round(summary.ascent_m), "m", null]);
    stats.push(["Descent", Math.round(summary.descent_m), "m", null]);
  }
  if (summary.calories) stats.push(["Calories", Math.round(summary.calories), "kcal", null]);
  if (summary.avg_temperature !== null && summary.avg_temperature !== undefined) {
    stats.push(["Avg temp", summary.avg_temperature.toFixed(1), "°C", null]);
  }

  grid.innerHTML = stats
    .map(function (s) {
      return (
        '<div class="stat ' + (s[3] || "") + '">' +
        '<div class="k">' + s[0] + "</div>" +
        '<div class="v">' + s[1] + (s[2] ? '<span class="u">' + s[2] + "</span>" : "") + "</div>" +
        "</div>"
      );
    })
    .join("");

  /* ---------------- shared axis config ---------------- */

  function tickTime(value) {
    var s = Math.round(value);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    return h ? h + ":" + pad(m) + ":" + pad(sec) : m + ":" + pad(sec);
  }

  function timeAxis() {
    return {
      type: "linear",
      title: { display: true, text: "elapsed time" },
      ticks: { maxTicksLimit: 8, callback: function (value) { return tickTime(value); } },
      grid: { color: "rgba(34, 48, 63, 0.6)" },
    };
  }

  function speedAxis() {
    return {
      title: { display: true, text: "km/h" },
      beginAtZero: true,
      grid: { color: "rgba(34, 48, 63, 0.6)" },
    };
  }

  function xy(ts, values, scale) {
    var out = [];
    for (var i = 0; i < ts.length; i++) {
      var v = values[i];
      if (v === null || v === undefined) continue;
      out.push({ x: ts[i], y: scale ? scale(v) : v });
    }
    return out;
  }

  var toKmh = function (v) { return v * 3.6; };

  /* ---------------- histogram ---------------- */

  if (histogram && histogram.bins && hasChart) {
    var labels = histogram.bins.map(function (b) {
      return ((b.lo + b.hi) / 2 * 3.6).toFixed(1);
    });
    var minutes = histogram.bins.map(function (b) { return b.seconds / 60; });
    var maxSeconds = Math.max.apply(null, histogram.bins.map(function (b) { return b.seconds; })) || 1;

    var palette = histogram.bins.map(function (b) {
      var ratio = (b.lo + b.hi) / 2 / (histogram.max_ms || 1);
      var alpha = 0.35 + 0.65 * Math.min(1, ratio);
      return "rgba(47, 212, 196, " + alpha.toFixed(2) + ")";
    });

    new Chart(document.getElementById("histogram"), {
      type: "bar",
      data: {
        labels: labels,
        datasets: [{
          label: "minutes",
          data: minutes,
          backgroundColor: palette,
          borderWidth: 0,
          barPercentage: 1.0,
          categoryPercentage: 1.0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: function (items) {
                var bin = histogram.bins[items[0].dataIndex];
                return (bin.lo * 3.6).toFixed(1) + " – " + (bin.hi * 3.6).toFixed(1) + " km/h";
              },
              label: function (item) {
                var bin = histogram.bins[item.dataIndex];
                return hms(bin.seconds) + "  (" + bin.pct.toFixed(1) + "% of the session)";
              },
            },
          },
        },
        scales: {
          x: {
            title: { display: true, text: "km/h" },
            grid: { display: false },
            ticks: { maxTicksLimit: 12 },
          },
          y: {
            title: { display: true, text: "minutes" },
            beginAtZero: true,
            grid: { color: "rgba(34, 48, 63, 0.6)" },
          },
        },
      },
    });

    document.getElementById("hist-note").textContent =
      "Time spent in each speed band · mean " + kmh(histogram.mean_ms) +
      " · median " + kmh(histogram.median_ms) +
      " · 95th percentile " + kmh(histogram.p95_ms) +
      " · peak band " + (histogram.mode_lo_ms * 3.6).toFixed(1) + "–" +
      (histogram.mode_hi_ms * 3.6).toFixed(1) + " km/h";
  } else {
    document.getElementById("hist-note").textContent = "No speed samples in this file.";
  }

  /* ---------------- speed trace + selectable rolling window ---------------- */

  var rollingWindows = trace.rolling_windows || {};
  var currentWindow = String(trace.window_s || 30);
  var speedChart = null;

  function windowLabel(seconds) {
    var value = Number(seconds);
    if (value < 60) return value + " s";
    var minutes = value / 60;
    return (minutes === Math.floor(minutes) ? minutes : minutes.toFixed(1)) + " min";
  }

  function rollingSeries(seconds) {
    var values = rollingWindows[String(seconds)];
    return values ? xy(trace.t, values, toKmh) : [];
  }

  function selectRollingWindow(seconds) {
    currentWindow = String(seconds);

    document.getElementById("rolling-legend").textContent =
      windowLabel(seconds) + " rolling average";

    if (speedChart) {
      speedChart.data.datasets[1].data = rollingSeries(seconds);
      speedChart.update();
    }

    var control = document.getElementById("rolling-control");
    if (control) {
      var buttons = control.querySelectorAll("button");
      for (var i = 0; i < buttons.length; i++) {
        var active = buttons[i].dataset.window === currentWindow;
        buttons[i].classList.toggle("is-active", active);
        buttons[i].setAttribute("aria-pressed", active ? "true" : "false");
      }
    }
  }

  if (hasChart && trace.t && trace.t.length) {
    speedChart = new Chart(document.getElementById("speed-trace"), {
      type: "line",
      data: {
        datasets: [
          {
            label: "instantaneous",
            data: xy(trace.t, trace.speed, toKmh),
            borderColor: "rgba(74, 107, 133, 0.85)",
            borderWidth: 1,
            pointRadius: 0,
            tension: 0,
          },
          {
            label: "rolling",
            data: rollingSeries(currentWindow),
            borderColor: "#2fd4c4",
            borderWidth: 2.5,
            pointRadius: 0,
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: function (items) { return tickTime(items[0].parsed.x); },
              label: function (item) {
                return item.dataset.label + ": " + item.parsed.y.toFixed(1) + " km/h";
              },
            },
          },
        },
        scales: { x: timeAxis(), y: speedAxis() },
      },
    });
  }

  // Reflect the opening window, then wire the picker. The buttons exist in the
  // markup already, so the control still reads correctly without JavaScript.
  selectRollingWindow(currentWindow);
  (trace.rolling_options || []).forEach(function (seconds) {
    var button = document.getElementById("window-" + seconds);
    if (button) {
      button.addEventListener("click", function () { selectRollingWindow(seconds); });
    }
  });

  /* ---------------- heart rate ---------------- */

  if (summary.has_hr && hasChart && trace.t && trace.t.length) {
    document.getElementById("hr-card").hidden = false;
    new Chart(document.getElementById("hr-trace"), {
      type: "line",
      data: {
        datasets: [{
          label: "heart rate",
          data: xy(trace.t, trace.hr),
          borderColor: "#ff6b6b",
          backgroundColor: "rgba(255, 107, 107, 0.12)",
          borderWidth: 1.6,
          pointRadius: 0,
          fill: true,
          tension: 0.2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: function (items) { return tickTime(items[0].parsed.x); },
              label: function (item) { return item.parsed.y + " bpm"; },
            },
          },
        },
        scales: {
          x: timeAxis(),
          y: {
            title: { display: true, text: "bpm" },
            grid: { color: "rgba(34, 48, 63, 0.6)" },
          },
        },
      },
    });
  }

  /* ---------------- elevation ---------------- */

  if (showElevation && summary.has_altitude && hasChart && trace.t && trace.t.length) {
    document.getElementById("elev-card").hidden = false;
    new Chart(document.getElementById("elev-trace"), {
      type: "line",
      data: {
        datasets: [{
          label: "altitude",
          data: xy(trace.t, trace.altitude),
          borderColor: "#ffb347",
          backgroundColor: "rgba(255, 179, 71, 0.14)",
          borderWidth: 1.6,
          pointRadius: 0,
          fill: true,
          tension: 0.2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: function (items) { return tickTime(items[0].parsed.x); },
              label: function (item) { return item.parsed.y.toFixed(0) + " m"; },
            },
          },
        },
        scales: {
          x: timeAxis(),
          y: {
            title: { display: true, text: "m" },
            grid: { color: "rgba(34, 48, 63, 0.6)" },
          },
        },
      },
    });
  }

  /* ---------------- splits ---------------- */

  if (splits.length) {
    document.getElementById("splits-card").hidden = false;
    document.getElementById("splits-body").innerHTML = splits
      .map(function (s) {
        return (
          "<tr>" +
          "<td>" + (s.distance_m >= 1000 ? (s.distance_m / 1000) + " km" : Math.round(s.distance_m) + " m") +
          " " + s.index + "</td>" +
          "<td>" + hms(s.duration_s) + "</td>" +
          "<td>" + kmh(s.avg_speed_ms) + "</td>" +
          "<td>" + pace(s.avg_speed_ms) + "</td>" +
          "<td>" + kmh(s.max_speed_ms) + "</td>" +
          "<td>" + (s.avg_hr === null || s.avg_hr === undefined ? "—" : Math.round(s.avg_hr)) + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  /* ---------------- route map ---------------- */

  function basemapCatalogue() {
    var node = document.getElementById("basemap-data");
    if (!node) return [];
    try {
      return JSON.parse(node.textContent) || [];
    } catch (err) {
      return [];
    }
  }

  // Offline fallback: an inline SVG outline of the route. Needs no tiles, no
  // network and no key, so the shape is always visible even with the phone
  // off-network.
  function renderSvgTrack(svg) {
    var lats = track.map(function (p) { return p[1]; });
    var lons = track.map(function (p) { return p[0]; });
    var latMid = (Math.min.apply(null, lats) + Math.max.apply(null, lats)) / 2;
    var k = Math.cos((latMid * Math.PI) / 180) || 1;
    var xs = lons.map(function (v) { return v * k; });

    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
    var minY = Math.min.apply(null, lats), maxY = Math.max.apply(null, lats);
    var spanX = (maxX - minX) || 1e-9;
    var spanY = (maxY - minY) || 1e-9;
    var scale = 92 / Math.max(spanX, spanY);
    var offX = (100 - spanX * scale) / 2;
    var offY = (100 - spanY * scale) / 2;

    var d = "";
    for (var i = 0; i < track.length; i++) {
      d += (i ? "L" : "M") +
        (offX + (xs[i] - minX) * scale).toFixed(2) + " " +
        (100 - (offY + (lats[i] - minY) * scale)).toFixed(2);
    }

    var startX = offX + (xs[0] - minX) * scale;
    var startY = 100 - (offY + (lats[0] - minY) * scale);
    var endX = offX + (xs[track.length - 1] - minX) * scale;
    var endY = 100 - (offY + (lats[track.length - 1] - minY) * scale);

    svg.innerHTML =
      '<path d="' + d + '" fill="none" stroke="#2fd4c4" stroke-width="1.1" ' +
      'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>' +
      '<circle cx="' + startX.toFixed(2) + '" cy="' + startY.toFixed(2) + '" r="1.8" fill="#2fd4c4"/>' +
      '<circle cx="' + endX.toFixed(2) + '" cy="' + endY.toFixed(2) + '" r="1.8" fill="#ffb347"/>';
    svg.hidden = false;
  }

  function renderLeafletTrack(container) {
    var catalogue = basemapCatalogue();
    if (typeof L === "undefined" || !catalogue.length) return false;

    var latlngs = track.map(function (point) { return [point[1], point[0]]; });

    // Leaflet measures the container at construction time, so reveal it first.
    container.hidden = false;
    var map = L.map(container, { scrollWheelZoom: false, zoomControl: true });

    var tileProblem = false;

    var layers = {};
    catalogue.forEach(function (base) {
      layers[base.key] = L.tileLayer(base.url, {
        attribution: base.attribution,
        maxZoom: base.max_zoom,
        subdomains: base.subdomains || "abc",
      });
      // Diagnostics: a device that is on the LAN but off the internet can reach
      // the app and not the tiles. Say so instead of showing a blank canvas.
      if (typeof layers[base.key].on === "function") {
        layers[base.key].on("tileerror", function () {
          if (tileProblem) return;
          tileProblem = true;
          var note = document.getElementById("track-note");
          if (note) {
            note.textContent += " · map tiles unreachable, route shown on a plain background";
          }
        });
      }
    });

    var current = catalogue[0].key;
    layers[current].addTo(map);

    L.polyline(latlngs, { color: "#2fd4c4", weight: 3, opacity: 0.9 }).addTo(map);
    L.circleMarker(latlngs[0], {
      radius: 6, color: "#04211f", weight: 2, fillColor: "#2fd4c4", fillOpacity: 1,
    }).bindTooltip("Start").addTo(map);
    L.circleMarker(latlngs[latlngs.length - 1], {
      radius: 6, color: "#04211f", weight: 2, fillColor: "#ffb347", fillOpacity: 1,
    }).bindTooltip("Finish").addTo(map);

    map.fitBounds(L.latLngBounds(latlngs), { padding: [22, 22] });

    // Re-measure once the browser has laid the card out. Defensive only: the
    // caller has already made the container visible, which is the part that
    // actually matters.
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(function () { map.invalidateSize(); });
    } else {
      map.invalidateSize();
    }

    function activate(key) {
      if (key === current || !layers[key]) return;
      map.removeLayer(layers[current]);
      layers[key].addTo(map);
      current = key;

      var control = document.getElementById("basemap-control");
      if (!control) return;
      var buttons = control.querySelectorAll("button");
      for (var i = 0; i < buttons.length; i++) {
        var on = buttons[i].dataset.basemap === current;
        buttons[i].classList.toggle("is-active", on);
        buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
      }
    }

    catalogue.forEach(function (base) {
      var button = document.getElementById("basemap-" + base.key);
      if (button) {
        button.addEventListener("click", function () { activate(base.key); });
      }
    });

    return true;
  }

  if (track.length > 1) {
    var mapEl = document.getElementById("map");
    var svgEl = document.getElementById("track");

    // Reveal the card BEFORE Leaflet is handed the container. A display:none
    // ancestor gives the map a 0x0 viewport, so Leaflet computes an empty tile
    // range and asks for nothing - and calling invalidateSize() while the card
    // is still hidden just recomputes 0x0. The symptom is a map that draws its
    // zoom control and attribution but never fetches a single tile.
    document.getElementById("track-card").hidden = false;

    var usedMap = false;
    try {
      usedMap = mapEl ? renderLeafletTrack(mapEl) : false;
    } catch (err) {
      usedMap = false;
      if (mapEl) mapEl.hidden = true;
    }

    if (!usedMap) {
      if (svgEl) renderSvgTrack(svgEl);
      var control = document.getElementById("basemap-control");
      if (control) control.hidden = true;
    }

    document.getElementById("track-note").textContent =
      "Green dot: start · amber dot: finish · " + track.length + " GPS points" +
      (usedMap ? " · tap the buttons above to change the background" : " plotted");
  }

  /* ---------------- file + delete ---------------- */

  var fileBits = [];
  if (meta.manufacturer) fileBits.push("Device: " + meta.manufacturer.replace(/_/g, " ") + (meta.device ? " " + meta.device : ""));
  if (report.points_total) {
    fileBits.push(report.points_total.toLocaleString() + " samples recorded, " +
      (report.points_charted || 0).toLocaleString() + " plotted");
  }
  document.getElementById("file-note").textContent = fileBits.join(" · ");

  var del = document.getElementById("delete-btn");
  if (del) {
    del.addEventListener("click", function () {
      if (!window.confirm("Delete this session and its uploaded file?")) return;
      fetch(del.dataset.deleteUrl, { method: "POST" })
        .then(function (response) {
          if (!response.ok) throw new Error("delete failed");
          window.location.href = del.dataset.homeUrl;
        })
        .catch(function () { window.alert("Could not delete the session."); });
    });
  }

  // -- sharing the session with other members -------------------------------
  var memberList = document.getElementById("member-list");
  if (memberList) {
    var memberUrl = memberList.dataset.memberUrl;

    function shareMember(body) {
      return fetch(memberUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (response) {
        if (response.ok) {
          window.location.reload();
          return;
        }
        return response
          .json()
          .catch(function () { return {}; })
          .then(function (data) {
            throw new Error(data.error || "Could not update the members.");
          });
      });
    }

    var addButton = document.getElementById("add-member-btn");
    var addSelect = document.getElementById("member-to-add");
    if (addButton && addSelect) {
      addButton.addEventListener("click", function () {
        if (!addSelect.value) return;
        shareMember({ add: addSelect.value }).catch(function (error) {
          window.alert(error.message);
        });
      });
    }

    memberList.addEventListener("click", function (event) {
      var button = event.target.closest("[data-remove]");
      if (!button) return;
      if (!window.confirm("Remove this member from the session?")) return;
      shareMember({ remove: button.dataset.remove }).catch(function (error) {
        window.alert(error.message);
      });
    });
  }
})();
