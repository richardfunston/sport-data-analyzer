"""Generate a synthetic canoeing .fit file for tests and demos.

The session is deterministic (fixed seed) so assertions can rely on it:
a 26-minute paddle with a warm-up, three effort blocks, a drink stop and a
finish surge, recorded at 1 Hz with heart rate, stroke rate, GPS and
altitude.

Requires ``fit_tool`` (development only):

    pip install -r requirements-dev.txt
    python tools/make_sample_fit.py tests/fixtures/canoe-session.fit
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import FileType, Manufacturer, Sport

import datetime as dt

SEED = 20240615
START = dt.datetime(2024, 6, 15, 8, 0, 0, tzinfo=dt.timezone.utc)
LAT0, LON0 = 51.7500, -1.2500
M_PER_DEG = 111_320.0

#: (duration_s, target_speed_ms, wobble_ms) - a realistic touring/racing paddle.
PHASES = [
    (120, 2.10, 0.15),   # warm-up
    (300, 3.10, 0.25),   # settle into rhythm
    (240, 3.90, 0.30),   # first push
    (90, 0.00, 0.00),    # drink stop
    (360, 3.20, 0.25),   # steady cruise
    (180, 4.30, 0.20),   # threshold block
    (120, 2.00, 0.15),   # ease off
    (150, 4.60, 0.15),   # finish surge
]


def fit_timestamp(when: dt.datetime) -> int:
    """fit_tool's date_time fields take Unix milliseconds."""
    return int(when.timestamp() * 1000)


def build_speeds(rng: random.Random) -> list[float]:
    """One speed sample per second, smoothed so it reads like a real trace."""
    raw: list[float] = []
    for duration, target, wobble in PHASES:
        for _ in range(duration):
            raw.append(max(0.0, target + rng.gauss(0.0, wobble)))

    # 7-sample centred smoothing removes the white noise but keeps the shape.
    smoothed: list[float] = []
    for i in range(len(raw)):
        lo = max(0, i - 3)
        hi = min(len(raw), i + 4)
        smoothed.append(sum(raw[lo:hi]) / (hi - lo))
    return smoothed


def build_hr(speeds: list[float]) -> list[int]:
    """Heart rate with a first-order lag behind effort."""
    hr = 88.0
    out: list[int] = []
    for speed in speeds:
        effort = min(1.0, speed / 5.0)
        target = 92.0 + effort * 78.0
        hr += (target - hr) * (1.0 / 45.0)
        out.append(int(round(hr)))
    return out


def build_route(speeds: list[float]) -> list[tuple[float, float]]:
    """Dead-reckon a winding river track from the speed profile."""
    lat, lon = LAT0, LON0
    heading = 0.0
    points: list[tuple[float, float]] = []

    for i, speed in enumerate(speeds):
        heading += 0.020 * math.sin(i / 50.0) + 0.010 * math.cos(i / 17.0)
        step = speed * 1.0
        lat += (step * math.cos(heading)) / M_PER_DEG
        lon += (step * math.sin(heading)) / (M_PER_DEG * math.cos(math.radians(lat)))
        points.append((lat, lon))
    return points


def build_altitude(count: int) -> list[float]:
    return [8.0 + 3.0 * math.sin(i / 300.0) + 1.5 * math.sin(i / 97.0) for i in range(count)]


def main(out_path: str) -> None:
    rng = random.Random(SEED)
    speeds = build_speeds(rng)
    heart_rates = build_hr(speeds)
    route = build_route(speeds)
    altitudes = build_altitude(len(speeds))

    distance = 0.0
    records: list[RecordMessage] = []

    for i, speed in enumerate(speeds):
        if i > 0:
            distance += (speeds[i - 1] + speed) / 2.0  # trapezoid over 1 s

        record = RecordMessage()
        record.timestamp = fit_timestamp(START + dt.timedelta(seconds=i))
        record.speed = round(speed, 3)
        record.distance = round(distance, 2)
        record.heart_rate = heart_rates[i]
        record.cadence = 0 if speed < 0.3 else int(round(26 + 21 * min(1.0, speed / 4.6)))
        record.position_lat = round(route[i][0], 7)
        record.position_long = round(route[i][1], 7)
        record.altitude = round(altitudes[i], 2)
        record.temperature = 17
        records.append(record)

    elapsed = float(len(speeds) - 1)
    moving = sum(1 for s in speeds if s > 0.5)
    ascents = sum(
        max(0.0, altitudes[i] - altitudes[i - 1]) for i in range(1, len(altitudes))
    )
    descents = sum(
        max(0.0, altitudes[i - 1] - altitudes[i]) for i in range(1, len(altitudes))
    )

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.WAHOO_FITNESS
    file_id.product = 28
    file_id.serial_number = 424242
    file_id.time_created = fit_timestamp(START)

    lap = LapMessage()
    lap.timestamp = fit_timestamp(START + dt.timedelta(seconds=elapsed))
    lap.start_time = fit_timestamp(START)
    lap.total_elapsed_time = elapsed
    lap.total_timer_time = float(moving)
    lap.total_distance = round(distance, 2)
    lap.avg_speed = round(distance / max(moving, 1), 4)
    lap.max_speed = round(max(speeds), 4)
    lap.avg_heart_rate = int(round(sum(heart_rates) / len(heart_rates)))
    lap.max_heart_rate = max(heart_rates)

    session = SessionMessage()
    session.timestamp = fit_timestamp(START + dt.timedelta(seconds=elapsed))
    session.start_time = fit_timestamp(START)
    session.sport = Sport.CANOEING
    session.total_elapsed_time = elapsed
    session.total_timer_time = float(moving)
    session.total_distance = round(distance, 2)
    session.avg_speed = round(distance / max(moving, 1), 4)
    session.max_speed = round(max(speeds), 4)
    session.avg_heart_rate = int(round(sum(heart_rates) / len(heart_rates)))
    session.max_heart_rate = max(heart_rates)
    session.avg_cadence = int(round(sum(r.cadence for r in records) / len(records)))
    session.max_cadence = max(r.cadence for r in records)
    session.total_ascent = int(round(ascents))
    session.total_descent = int(round(descents))
    session.total_calories = 312
    session.num_laps = 1

    activity = ActivityMessage()
    activity.timestamp = fit_timestamp(START + dt.timedelta(seconds=elapsed))
    activity.total_timer_time = elapsed
    activity.num_sessions = 1

    builder = FitFileBuilder(auto_define=True, min_string_size=50)
    builder.add(file_id)
    builder.add_all(records)
    builder.add(lap)
    builder.add(session)
    builder.add(activity)

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    builder.build().to_file(str(target))

    print(f"wrote {target} ({target.stat().st_size:,} bytes)")
    print(f"  samples      {len(records):,}")
    print(f"  elapsed      {elapsed:.0f} s")
    print(f"  moving       {moving} s")
    print(f"  distance     {distance / 1000:.2f} km")
    print(f"  avg speed    {distance / max(moving, 1) * 3.6:.2f} km/h")
    print(f"  max speed    {max(speeds) * 3.6:.2f} km/h")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: make_sample_fit.py <output.fit>")
    main(sys.argv[1])
