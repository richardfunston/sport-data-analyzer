"""Decode .FIT activity files into a normalized, columnar structure.

The result is column-oriented (parallel arrays) so it serialises to compact
JSON and feeds straight into the browser charting code.

The only dependency is ``fitdecode`` - pure Python, no compiled extensions.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import fitdecode

from .profiles import sub_sport_name, sport_name

__all__ = ["FitError", "parse_fit", "SERIES_KEYS", "MAX_SPEED_MS"]

#: Series carried through to the report, with their units.
SERIES_KEYS = (
    "speed",        # m/s
    "distance",     # m
    "hr",           # bpm
    "cadence",      # rpm / strokes per minute
    "altitude",     # m
    "lat",          # degrees
    "lon",          # degrees
    "temperature",  # degC
)

#: FIT stores geographic coordinates as 32-bit semicircles.
SEMICIRCLE_TO_DEG = 180.0 / (1 << 31)

#: No paddled craft exceeds this; anything higher is a decoding artefact.
MAX_SPEED_MS = 50.0

#: Plausible ranges used to drop corrupt samples. fitdecode already maps the
#: FIT "invalid" sentinel to None, but range guards catch the rest.
_RANGES: dict[str, tuple[float, float]] = {
    "speed": (0.0, MAX_SPEED_MS),
    "distance": (0.0, 1.0e7),
    "hr": (20.0, 260.0),
    "cadence": (0.0, 255.0),
    "altitude": (-500.0, 9000.0),
    "temperature": (-60.0, 60.0),
}


class FitError(ValueError):
    """Raised when an upload is not a readable FIT activity file."""


def _num(value: Any) -> float | None:
    """Coerce a FIT field value to a finite float, or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _sane(value: float | None, key: str) -> float | None:
    """Return ``value`` when it lies in the plausible range for its field."""
    if value is None:
        return None
    lo, hi = _RANGES[key]
    return value if lo <= value <= hi else None


def _coord(value: Any) -> float | None:
    """Convert a FIT coordinate to degrees.

    fitdecode reports position fields in semicircles. A few writers store
    degrees directly, so a value already inside the degree range is left
    alone.
    """
    v = _num(value)
    if v is None:
        return None
    if abs(v) > 180.0:
        v *= SEMICIRCLE_TO_DEG
    return v if -180.0 <= v <= 180.0 else None


def _device_label(file_id: dict[str, Any], devices: list[dict[str, Any]]) -> str | None:
    """Best human-readable device name we can find in the file."""
    manufacturer = file_id.get("manufacturer")
    if manufacturer is not None:
        for dev in devices:
            name = dev.get("product_name")
            if name and dev.get("manufacturer") == manufacturer:
                return str(name)
    for dev in devices:
        if dev.get("product_name"):
            return str(dev["product_name"])
    if manufacturer is not None:
        return str(manufacturer).replace("_", " ").title()
    return None


def _pick_session(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Multisport files carry several sessions; use the longest one."""
    if not sessions:
        return {}

    def weight(s: dict[str, Any]) -> float:
        return _num(s.get("total_elapsed_time")) or _num(s.get("total_timer_time")) or 0.0

    return max(sessions, key=weight)


def parse_fit(source: Any) -> dict[str, Any]:
    """Decode a FIT activity file.

    ``source`` may be a path, ``bytes``, or a binary file object.

    Returns ``{"meta": ..., "series": ..., "session": ...}`` where ``series``
    holds parallel arrays keyed by :data:`SERIES_KEYS` plus ``t`` (seconds
    since the activity start).
    """
    records: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    file_id: dict[str, Any] = {}
    sport: dict[str, Any] = {}

    try:
        with fitdecode.FitReader(source) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                name = frame.name
                if name == "record":
                    records.append({f.name: f.value for f in frame.fields})
                elif name == "session":
                    sessions.append({f.name: f.value for f in frame.fields})
                elif name == "device_info":
                    devices.append({f.name: f.value for f in frame.fields})
                elif name == "file_id" and not file_id:
                    file_id = {f.name: f.value for f in frame.fields}
                elif name == "sport" and not sport:
                    sport = {f.name: f.value for f in frame.fields}
    except Exception as exc:  # fitdecode raises a family of FitError subclasses
        raise FitError(f"not a readable FIT file ({exc})") from exc

    if not records:
        raise FitError("FIT file contains no activity records")

    session = _pick_session(sessions)

    stamps = [r["timestamp"] for r in records if isinstance(r.get("timestamp"), dt.datetime)]
    if not stamps:
        raise FitError("FIT file contains no timestamps")

    start = min(stamps)
    session_start = session.get("start_time")
    if isinstance(session_start, dt.datetime) and session_start < start:
        start = session_start

    t: list[float] = []
    series: dict[str, list[float | None]] = {k: [] for k in SERIES_KEYS}

    for rec in records:
        when = rec.get("timestamp")
        if not isinstance(when, dt.datetime):
            continue
        t.append((when - start).total_seconds())

        speed = _sane(_num(rec.get("enhanced_speed")), "speed")
        if speed is None:
            speed = _sane(_num(rec.get("speed")), "speed")
        series["speed"].append(speed)

        series["distance"].append(_sane(_num(rec.get("distance")), "distance"))
        series["hr"].append(_sane(_num(rec.get("heart_rate")), "hr"))
        series["cadence"].append(_sane(_num(rec.get("cadence")), "cadence"))

        altitude = _sane(_num(rec.get("enhanced_altitude")), "altitude")
        if altitude is None:
            altitude = _sane(_num(rec.get("altitude")), "altitude")
        series["altitude"].append(altitude)

        lat, lon = _coord(rec.get("position_lat")), _coord(rec.get("position_long"))
        series["lat"].append(lat if lat is not None and abs(lat) <= 90.0 else None)
        series["lon"].append(lon)

        series["temperature"].append(_sane(_num(rec.get("temperature")), "temperature"))

    if len(t) < 2:
        raise FitError("FIT file has too few timestamped records to analyse")

    _fill_gaps(t, series)

    created = file_id.get("time_created")
    meta = {
        "start_time": start.isoformat(),
        "sport": sport_name(sport.get("sport") or session.get("sport")),
        "sub_sport": sub_sport_name(sport.get("sub_sport") or session.get("sub_sport")),
        "manufacturer": file_id.get("manufacturer"),
        "product": file_id.get("product"),
        "serial_number": file_id.get("serial_number"),
        "file_created": created.isoformat() if isinstance(created, dt.datetime) else None,
        "device": _device_label(file_id, devices),
        "sample_count": len(t),
    }

    return {"meta": meta, "series": {"t": t, **series}, "session": _session_extras(session)}


def _session_extras(session: dict[str, Any]) -> dict[str, float | None]:
    """Device-reported totals that we cannot derive from the sample stream."""
    return {
        "calories": _num(session.get("total_calories")),
        "total_elapsed_time": _num(session.get("total_elapsed_time")),
        "total_timer_time": _num(session.get("total_timer_time")),
        "total_distance": _num(session.get("total_distance")),
        "total_ascent": _num(session.get("total_ascent")),
        "total_descent": _num(session.get("total_descent")),
        "avg_temperature": _num(session.get("avg_temperature")),
        "max_temperature": _num(session.get("max_temperature")),
    }


def _fill_gaps(t: list[float], series: dict[str, list[float | None]]) -> None:
    """Repair missing speed/distance samples in place.

    Devices drop the occasional field. Speed is recovered from the distance
    delta where possible, and distance is integrated from speed where the
    device never recorded it (common on indoor or GPS-less paddles).

    The first distance sample is anchored before integrating, otherwise the
    cumulative total would be short by one sample's worth of distance.
    """
    speed, distance = series["speed"], series["distance"]
    count = len(t)

    if distance[0] is None:
        if (
            count > 1
            and distance[1] is not None
            and speed[1] is not None
            and t[1] > t[0]
        ):
            distance[0] = distance[1] - speed[1] * (t[1] - t[0])
        else:
            distance[0] = 0.0

    # Carry the last known odometer reading across gaps: a run of samples with
    # neither speed nor distance must not break the samples that follow it.
    last_known = distance[0]

    for i in range(1, count):
        dt_i = t[i] - t[i - 1]
        if dt_i > 0:
            previous = distance[i - 1]
            if speed[i] is None and distance[i] is not None and previous is not None:
                speed[i] = _sane((distance[i] - previous) / dt_i, "speed")

            if distance[i] is None and speed[i] is not None:
                distance[i] = last_known + speed[i] * dt_i

        if distance[i] is not None:
            last_known = distance[i]

    if speed and speed[0] is None and count > 1:
        speed[0] = speed[1]
