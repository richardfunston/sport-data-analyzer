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

## Exposing it to the internet

It has its own accounts and sessions, so it is safe to expose directly — the
proxy-level basic auth layer described below is optional defence in depth, not
the thing keeping people out.

```
internet → Nginx Proxy Manager (192.168.10.52 :80/:443)
         → http://192.168.10.45:8090   (gunicorn, bound 0.0.0.0:8090)
```

gunicorn binds all interfaces so the proxy can reach it. Two consequences worth
knowing:

- **LAN clients can reach `:8090` directly**, bypassing any basic auth applied
  at the proxy. The app's own login still applies, which is why authentication
  lives in the app and the proxy layer is only a second gate.
- Give the app a **dedicated hostname** (`paddle.example.com`) when you can. It
  generates root-relative URLs, so mounting it under a path only needs the proxy
  to send `X-Forwarded-Prefix` — which is what Caddy does for the LAN `/sport/`
  route. Both mounts are served by the same code.

Let's Encrypt's HTTP-01 challenge needs port 80 reachable from the internet and
the hostname resolving publicly. If your public IP is dynamic, point DNS at a
dynamic-DNS name rather than a literal address.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SDA_DATA_DIR` | `/var/lib/sport-data-analyzer` | Uploads, the SQLite index, and the generated signing key |
| `SDA_MAX_UPLOAD_BYTES` | `67108864` (64 MB) | Per-request upload ceiling |
| `SDA_SECRET_KEY` | generated into `$SDA_DATA_DIR/secret_key` | Session-cookie signing key |
| `SDA_BASE_PATH` | *(empty)* | Prefix, if you are not using `X-Forwarded-Prefix` |
| `SDA_INITIAL_PADDLER_EMAIL` | *(unset)* | First admin, created only when there are no paddlers |
| `SDA_INITIAL_PADDLER_NAME` | *(unset)* | …their display name |
| `SDA_INITIAL_PADDLER_PASSWORD` | *(unset)* | …their password. Never commit it |

## Accounts

Every page except `/healthz` needs a signed-in paddler. A session is a *shared*
record: it carries a list of paddlers, and each paddler sees only the sessions
they are linked to. A two-paddler canoe is one session linked twice.

Create the first paddler on a live host:

```bash
cd /opt/sport-data-analyzer
SDA_INITIAL_PADDLER_PASSWORD='…' .venv/bin/python -m app.cli create-paddler \
    --email you@example.com --name 'Your Name' --admin
```

Or let a fresh install bootstrap itself from the three
`SDA_INITIAL_PADDLER_*` variables above. That runs **only** when the users table
is empty, so a restart can never resurrect a deleted account or reset a password.

After that an admin manages everyone at `/admin/paddlers`: create paddlers,
change passwords, promote or deactivate. Each report page carries a paddlers card
for sharing that session with someone else.

Four things worth knowing:

- **A password change signs that paddler out everywhere.** The cookie carries a
  `session_epoch`; changing the password bumps it, so a stolen cookie does not
  outlive the password it belonged to.
- **Deactivating a paddler takes effect on their next request**, not at their
  next sign-in — the row is re-read every request rather than trusted from the
  cookie.
- **The last active admin cannot be demoted or deactivated.** That would lock
  everyone out of the admin screen with no way back in through the UI.
- **Sharing is explicit.** Being signed in grants nothing on its own; an unlinked
  session answers `404` rather than `403`, because a `403` confirms it exists.

Sessions last 12 hours and slide on each request. Passwords are stored as scrypt
hashes — never in plain text, and the first one is never committed.

## HTTP surface

| Method | Path | Purpose |
|---|---|---|
| `GET`/`POST` | `/login` | Sign in |
| `POST` | `/logout` | Sign out |
| `GET` | `/` | Upload page and your recent sessions |
| `POST` | `/api/upload` | Multipart `files`; returns a report link per file |
| `GET` | `/a/<id>` | The report page |
| `GET` | `/a/<id>/download` | The original `.fit`, byte for byte |
| `POST` | `/api/a/<id>/delete` | Delete the session |
| `POST` | `/api/a/<id>/paddlers` | `{"add": id}` or `{"remove": id}` — share the session |
| `GET` | `/admin/paddlers` | Admin: list, create, change passwords |
| `POST` | `/admin/paddlers/new` | Admin: create a paddler |
| `POST` | `/admin/paddlers/<id>/password` | Admin: set a new password |
| `POST` | `/admin/paddlers/<id>/update` | Admin: rename, promote, deactivate |
| `GET` | `/healthz` | Liveness. Public, so it reports nothing else |

Everything except `/login` and `/healthz` requires a session. `/admin/*` also
requires an admin; a signed-in non-admin gets `403`, an anonymous caller gets a
redirect to `/login` rather than a `403` that would confirm the route exists.

Re-uploading a file you already have is detected by SHA-256 and returns the
existing report instead of a duplicate — **and links the uploader to it**, so the
second paddler in a canoe adding the same file from their own watch sees the
session rather than being told it is a duplicate.

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
├── sport.db                        # SQLite: sessions, paddlers, and the links
├── secret_key                      # generated once, mode 0600
└── uploads/
    ├── <id>.fit                    # the original upload, unmodified
    └── <id>.report.json.gz         # the computed report payload
```

Three tables: `activities` (one row per session), `users` (one row per paddler),
and `activity_paddlers`, the join between them. The join is what makes a session
shared — a canoe with two paddlers is one activity linked twice, and a paddler
sees exactly the activities they are linked to.

`users.email` is declared `COLLATE NOCASE`. SQLite's default `TEXT` collation is
`BINARY`, so a plain `UNIQUE` accepts `RPF@…` beside `rpf@…` and splits one
person into two accounts whose login depends on which casing they type.

Reports are cached rather than recomputed per request. A `REPORT_VERSION` is
stored inside each payload; opening a report written by an older version of the
analysis regenerates it from the original `.fit` file and rewrites the cache, so
improving a statistic upgrades sessions you uploaded months ago.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest            # 254 tests
```

Coverage includes unit tests for every derived statistic, decoder tests against
a synthetic canoeing session and a real 13,471-record Wahoo ELEMNT ride,
end-to-end tests of upload, dedupe, report rendering, deletion and rebuilding,
and the account model: password hashing, the session epoch, per-paddler
visibility (including the 404 for an unlinked session), the admin screen's
lockout guards, the CLI, and that the backfill is wired into the app factory
rather than merely available in the store.

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
