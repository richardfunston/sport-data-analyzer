/* Upload page: file picking, drag & drop, progress, and inline results. */
(function () {
  "use strict";

  var zone = document.getElementById("dropzone");
  if (!zone) return;

  var input = document.getElementById("file-input");
  var progress = document.getElementById("progress");
  var fill = document.getElementById("progress-fill");
  var label = document.getElementById("progress-label");
  var results = document.getElementById("results");
  var endpoint = zone.dataset.uploadUrl;

  /* ---------- formatting ---------- */

  function hms(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    var total = Math.round(seconds);
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    return h ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
  }

  function km(metres) {
    return metres === null || metres === undefined ? "—" : (metres / 1000).toFixed(2) + " km";
  }

  function kmh(ms) {
    return ms === null || ms === undefined ? "—" : (ms * 3.6).toFixed(1) + " km/h";
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  /* ---------- rendering ---------- */

  function showProgress(show, pct, text) {
    progress.classList.toggle("on", show);
    if (pct !== undefined) fill.style.width = pct + "%";
    if (text !== undefined) label.textContent = text;
  }

  function renderResults(items) {
    results.innerHTML = items
      .map(function (item) {
        if (!item.ok) {
          return (
            '<div class="result err">' +
            '<div><div class="name">' + escapeHtml(item.filename) + "</div>" +
            '<div class="meta">' + escapeHtml(item.error) + "</div></div></div>"
          );
        }
        var s = item.summary || {};
        var meta = item.meta || {};
        var bits = [
          km(s.distance_m),
          hms(s.moving_s),
          "avg " + kmh(s.avg_moving_speed_ms),
          "max " + kmh(s.max_speed_ms),
        ];
        if (s.avg_hr) bits.push("HR " + Math.round(s.avg_hr));
        return (
          '<div class="result">' +
          '<div><div class="name">' + escapeHtml(item.filename) + "</div>" +
          '<div class="meta">' + escapeHtml(bits.join(" · ")) +
          (item.created ? "" : " · already uploaded") + "</div></div>" +
          '<a class="btn small" href="' + item.url + '">Open report</a>' +
          "</div>"
        );
      })
      .join("");
  }

  /* ---------- upload ---------- */

  function upload(files) {
    if (!files || !files.length) return;

    var form = new FormData();
    var count = 0;
    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      if (!/\.fit$/i.test(file.name)) continue;
      form.append("files", file, file.name);
      count++;
    }
    if (!count) {
      renderResults([{ filename: "selection", ok: false, error: "no .fit files in that selection" }]);
      return;
    }

    var request = new XMLHttpRequest();
    request.open("POST", endpoint, true);

    request.upload.onprogress = function (event) {
      if (!event.lengthComputable) return;
      showProgress(true, Math.round((event.loaded / event.total) * 100), "Uploading " + count + " file(s)…");
    };

    request.onload = function () {
      var payload = null;
      try {
        payload = JSON.parse(request.responseText);
      } catch (err) {
        payload = null;
      }
      showProgress(false);

      if (payload && payload.results) {
        renderResults(payload.results);
      } else if (payload && payload.error) {
        renderResults([{ filename: "upload", ok: false, error: payload.error }]);
      } else {
        renderResults([{ filename: "upload", ok: false, error: "server returned " + request.status }]);
      }
    };

    request.onerror = function () {
      showProgress(false);
      renderResults([{ filename: "upload", ok: false, error: "network error — is the server reachable?" }]);
    };

    showProgress(true, 0, "Uploading " + count + " file(s)…");
    request.send(form);
  }

  /* ---------- wiring ---------- */

  input.addEventListener("change", function () {
    upload(input.files);
    input.value = "";
  });

  ["dragenter", "dragover"].forEach(function (name) {
    zone.addEventListener(name, function (event) {
      event.preventDefault();
      zone.classList.add("is-over");
    });
  });

  ["dragleave", "drop"].forEach(function (name) {
    zone.addEventListener(name, function (event) {
      event.preventDefault();
      zone.classList.remove("is-over");
    });
  });

  zone.addEventListener("drop", function (event) {
    var dt = event.dataTransfer;
    if (dt && dt.files) upload(dt.files);
  });
})();
