# Sport Data Analyzer

Upload a `.fit` file straight from your phone and get a full session report for
it: a speed histogram, a speed trace with a rolling average, and the overall
averages and maxima that actually matter for a paddling session.

Built for Wahoo ELEMNT recordings, works with anything that writes a standard
FIT activity file (Garmin, Suunto, Coros, phone apps).

```
phone → pick .fit → upload → report page
```

## What the report shows

**Overall** — distance, moving time, elapsed time, stopped time, average speed
(moving and total), max speed, average pace and best pace (min/km), average and
max heart rate, average and max cadence, ascent/descent, calories, temperature.

**Speed distribution** — a histogram of *time spent* in each speed band rather
than a count of samples, because an hour spent crawling into wind should be
visible as an hour. Annotated with the time-weighted mean, median, 95th
percentile and the band you spent longest in.

**Speed trace** — instantaneous speed plotted against elapsed time, with a
trailing rolling average drawn over the top. The rolling line is what makes the
underlying effort pattern readable through GPS and paddle-stroke noise, and the
window is **selectable from 30 s to 10 min** so you can read short surges or the
shape of a whole session. Every window is averaged over the full-resolution
series at build time, so switching is instant and never re-smooths an
already-smoothed line.

**Heart rate** — full HR trace, when the file has it.

**Elevation** — altitude profile, when the file has it. This is deliberately
**withheld for water sports** (canoeing, kayaking, paddling, rowing, swimming,
SUP, sailing, surfing…): on water the altitude trace is surface noise, not
terrain, so waves and GPS drift read as climbing. The statistics are still
computed and stored — only the panel is withheld, so a river trip can be put
back with one flag.

**Route** — the GPS track drawn on a real map background with a **Streets /
Satellite / Terrain / Dark** switcher. All four providers are key-free, which is
the point for a self-hosted app: no API key, no billing account, no signup. If
Leaflet or the network is unavailable the track falls back to an inline SVG
outline, so the shape is always visible.

**Splits** — per-kilometre time, average speed, pace, max speed and average HR.

Charts are downsampled with [LTTB](https://github.com/sveinn-steinarsson/flot-downsample)
before they reach the browser, so a 13,000-sample recording stays responsive on
a phone while keeping its peaks.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
SDA_DATA_DIR=./data flask --app wsgi run --port 8090
```

Then open <http://127.0.0.1:8090/>.

### Production-ish

```bash
pip install -r requirements.txt
SDA_DATA_DIR=/var/lib/sport-data-analyzer \
  gunicorn --workers 2 --bind 127.0.0.1:8090 wsgi:application
```

`deploy/sport-data-analyzer.service` is a systemd unit, and
`deploy/Caddyfile.example` mounts the app under `/sport/` behind Caddy.

`deploy/ci.workflow.yml` is a GitHub Actions workflow. It lives there rather
than in `.github/workflows/` because pushing a workflow file needs a token with
the **Workflows** permission; the header of the file has the one-line command to
activate it.

### Using Google Maps instead

The map uses Leaflet with key-free tile providers (OpenStreetMap, Esri,
OpenTopoMap, CARTO). Google Maps needs a **Maps JavaScript API key from a
billing-enabled Cloud project**, and the key must be restricted to the origin
the app is served from. The swap is confined to `app/basemaps.py` and the map
block in `report.js`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SDA_DATA_DIR` | `/var/lib/sport-data-analyzer` | Uploads and the SQLite index |
| `SDA_MAX_UPLOAD_BYTES` | `67108864` (64 MB) | Per-request upload ceiling |
| `SDA_SECRET_KEY` | generated | Flask secret |
| `SDA_BASE_PATH` | *(empty)* | Prefix, if you are not using `X-Forwarded-Prefix` |

## HTTP surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Upload page and recent sessions |
| `POST` | `/api/upload` | Multipart `files`; returns a report link per file |
| `GET` | `/a/<id>` | The report page |
| `GET` | `/a/<id>/download` | The original `.fit`, byte for byte |
| `POST` | `/api/a/<id>/delete` | Delete the session |
| `GET` | `/healthz` | Liveness and activity count |

Re-uploading a file you already have is detected by SHA-256 and returns the
existing report instead of a duplicate.

## How the stats are computed

The interesting decisions, all of which have tests:

- **Time weighting.** Averages are integrated over elapsed time, not averaged
  across samples, so an irregular recording cadence does not bias the result.
- **Moving vs stopped.** A sample counts as moving above 0.5 m/s (1.8 km/h).
- **Auto-pause.** Wahoo devices stop writing records when you stop, which
  leaves multi-minute holes in the timestamps. Each sample's duration is capped
  at three times the recording cadence, so a 42-minute coffee stop is not
  credited as paddling. Without this, moving time came out ~18% too high on a
  real ELEMNT file.
- **Missing fields.** Speed is recovered from the distance delta when a device
  omits it, and distance is integrated from speed when a device never recorded
  it (common on indoor or GPS-less paddles).
- **Elevation.** Altitude is smoothed over five samples and changes under 1 m
  are ignored, so GPS jitter is not reported as climbing.
- **Sport names.** `fitdecode` ships a snapshot of the FIT profile and returns
  a bare integer for newer enum values — canoeing is `88`, which lands outside
  that snapshot. `app/profiles.py` carries the full official table.
- **Rolling windows.** Every window is averaged over the full-resolution series
  and only then downsampled; averaging an already-downsampled series smears the
  trace. The average is deliberately hoisted out of the downsample loop —
  computing it inline recomputes the whole series once per plotted point, which
  took the 13,471-record fixture from 0.5 s to 105 s.
- **Water sports.** Session type decides presentation, not analysis. Elevation
  is withheld for anything on water, and the sub-sport is checked too so a
  whitewater or open-water run recorded under a generic parent sport is caught.
  Additions go in `app/profiles.py`.

## Storage

```
$SDA_DATA_DIR/
├── sport.db                        # SQLite index: one row per session
└── uploads/
    ├── <id>.fit                    # the original upload, unmodified
    └── <id>.report.json.gz         # the computed report payload
```

Reports are cached rather than recomputed per request. A `REPORT_VERSION` is
stored inside each payload; opening a report written by an older version of the
analysis regenerates it from the original `.fit` file and rewrites the cache, so
improving a statistic upgrades sessions you uploaded months ago.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest            # 129 tests
```

Coverage includes unit tests for every derived statistic, decoder tests against
a synthetic canoeing session and a real 13,471-record Wahoo ELEMNT ride, and
end-to-end tests of upload, dedupe, report rendering, deletion and rebuilding.

Two fixtures live in `tests/fixtures/`:

- `canoe-session.fit` — synthetic, deterministic, generated by
  `tools/make_sample_fit.py` (26 min paddling session with a drink stop)
- `real-wahoo-elemnt.fit` — a real Wahoo ELEMNT ride, used as a regression test
  for the decoder and the auto-pause handling

Frontend logic can be verified without a browser:

```bash
node tools/verify_report_js.js payload.json app/static/js/report.js
```

Analysis performance can be checked on a real file:

```bash
PYTHONPATH=. python tools/profile_report.py tests/fixtures/real-wahoo-elemnt.fit
```

## Layout

```
app/
├── analysis.py     # statistics: histogram, rolling mean, splits, downsampling
├── fitio.py        # FIT decoding into columnar series
├── profiles.py     # FIT enum tables (sport, sub-sport) + water-sport sets
├── basemaps.py     # key-free tile providers for the route map
├── storage.py      # SQLite index + on-disk blobs
├── routes.py       # HTTP surface
├── formatting.py   # human-readable units
├── templates/      # Jinja pages
└── static/         # CSS, upload JS, report JS, vendored Chart.js + Leaflet
tools/
├── make_sample_fit.py    # synthetic canoeing fixture generator
├── verify_report_js.js   # browserless frontend check
├── profile_report.py     # time and profile the report build
├── inspect_fit.py        # dump the structure of a .fit file
└── w01.sh                # deployment helpers
```

## Licence

MIT — see [LICENSE](LICENSE).
