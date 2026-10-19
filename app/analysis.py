"""Derive paddling-session statistics from a parsed FIT activity.

Everything here works in SI units (metres, metres/second, seconds). The
browser layer is responsible for km/h and min/km presentation.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from .profiles import is_paddling

#: Speed at or below this counts as stationary (m/s). 0.5 m/s ~ 1.8 km/h.
MOVING_SPEED_MS = 0.5

#: Default window for the "averaged speed trace" rolling mean (seconds).
ROLLING_WINDOW_S = 30

#: Target number of bars in the speed histogram.
HISTOGRAM_BINS = 24

#: Altitude change below this is GPS noise, not a climb (metres).
ELEVATION_THRESHOLD_M = 1.0

#: Samples kept when sending a series to the browser for charting.
MAX_CHART_POINTS = 1200

#: Bumped whenever a change to the analysis alters stored reports. Stored
#: reports carrying an older version are rebuilt from the original .fit file
#: the next time they are opened.
REPORT_VERSION = 2


def _deltas(t: Sequence[float]) -> list[float]:
    """Per-sample time step, in seconds.

    A sample's weight is the interval that *follows* it, so the final sample
    carries no weight - it has no duration of its own. For a uniformly
    recorded session this keeps ``sum(deltas) == t[-1] - t[0]``.

    Devices with auto-pause (Wahoo among them) simply stop writing records
    while you are stationary, which leaves multi-minute holes in the
    timestamps. Attributing a whole hole to the last sample before it would
    count stopped time as moving, so each step is capped at a small multiple
    of the recording cadence.
    """
    n = len(t)
    if n < 2:
        return [0.0] * n

    steps = [max(0.0, t[i + 1] - t[i]) for i in range(n - 1)]
    steps.append(0.0)

    positive = sorted(s for s in steps if s > 0)
    if positive:
        cadence = positive[len(positive) // 2]
        cap = max(3.0 * cadence, 1.5)
        steps = [min(s, cap) for s in steps]

    return steps


def _weighted(values: Sequence[float | None], weights: Sequence[float]) -> float | None:
    """Time-weighted mean, ignoring missing samples."""
    total_w = 0.0
    total = 0.0
    for value, weight in zip(values, weights):
        if value is None or weight <= 0:
            continue
        total += value * weight
        total_w += weight
    return total / total_w if total_w > 0 else None


def _max_of(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _quantile(sorted_values: Sequence[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return sorted_values[int(pos)]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


def elevation_gain(
    t: Sequence[float],
    altitude: Sequence[float | None],
    threshold: float = ELEVATION_THRESHOLD_M,
) -> tuple[float | None, float | None]:
    """Total ascent and descent in metres.

    Altitude is smoothed first (5-sample mean) and changes smaller than
    ``threshold`` are discarded, so GPS jitter does not inflate the totals.
    """
    present = [(i, v) for i, v in enumerate(altitude) if v is not None]
    if len(present) < 3:
        return None, None

    idx = [i for i, _ in present]
    raw = [v for _, v in present]

    window = 5
    smoothed: list[float] = []
    for i in range(len(raw)):
        lo = max(0, i - window // 2)
        hi = min(len(raw), i + window // 2 + 1)
        chunk = raw[lo:hi]
        smoothed.append(sum(chunk) / len(chunk))

    ascent = descent = 0.0
    anchor = smoothed[0]
    for value in smoothed[1:]:
        change = value - anchor
        if change >= threshold:
            ascent += change
            anchor = value
        elif change <= -threshold:
            descent += -change
            anchor = value
    return ascent, descent


def moving_weights(
    speed: Sequence[float | None], weights: Sequence[float]
) -> list[float]:
    """Weights that keep only samples where the craft is actually moving."""
    if not any(v is not None for v in speed):
        return list(weights)
    return [
        w if (v is not None and v > MOVING_SPEED_MS) else 0.0
        for v, w in zip(speed, weights)
    ]


def summarize(activity: dict[str, Any]) -> dict[str, Any]:
    """Overall averages and maxima for the session."""
    series = activity["series"]
    t = series["t"]
    dts = _deltas(t)

    speed = series["speed"]
    distance = series["distance"]
    hr = series["hr"]
    cadence = series["cadence"]
    altitude = series["altitude"]

    elapsed = t[-1] - t[0] if len(t) > 1 else 0.0

    total_distance: float | None = None
    if distance and distance[-1] is not None and distance[0] is not None:
        span = distance[-1] - distance[0]
        if span > 0:
            total_distance = span
    if total_distance is None:
        integrated = sum(v * w for v, w in zip(speed, dts) if v is not None)
        total_distance = integrated if integrated > 0 else 0.0

    active = moving_weights(speed, dts)
    moving_time = sum(active)
    if moving_time <= 0:
        moving_time = elapsed
        active = list(dts)

    max_speed = _max_of(speed)
    speeds = sorted(v for v in speed if v is not None)

    ascent, descent = elevation_gain(t, altitude)

    return {
        "elapsed_s": elapsed,
        "moving_s": moving_time,
        "stopped_s": max(0.0, elapsed - moving_time),
        "distance_m": total_distance,
        "avg_speed_ms": total_distance / elapsed if elapsed > 0 else None,
        "avg_moving_speed_ms": total_distance / moving_time if moving_time > 0 else None,
        "max_speed_ms": max_speed,
        "speed_p95_ms": _quantile(speeds, 0.95),
        "speed_median_ms": _quantile(speeds, 0.50),
        "avg_hr": _weighted(hr, active),
        "avg_hr_all": _weighted(hr, dts),
        "max_hr": _max_of(hr),
        "avg_cadence": _weighted(cadence, active),
        "max_cadence": _max_of(cadence),
        "ascent_m": ascent,
        "descent_m": descent,
        "min_altitude_m": min((v for v in altitude if v is not None), default=None),
        "max_altitude_m": _max_of(altitude),
        "avg_temperature": _weighted(series["temperature"], dts),
        "max_temperature": _max_of(series["temperature"]),
        "calories": activity.get("session", {}).get("calories"),
        "has_hr": any(v is not None for v in hr),
        "has_cadence": any(v is not None for v in cadence),
        "has_gps": any(v is not None for v in series["lat"]),
        "has_altitude": any(v is not None for v in altitude),
    }


def speed_histogram(
    t: Sequence[float],
    speed: Sequence[float | None],
    bins: int = HISTOGRAM_BINS,
) -> dict[str, Any] | None:
    """Time spent in each speed band, plus distribution statistics."""
    dts = _deltas(t)
    samples = [(v, w) for v, w in zip(speed, dts) if v is not None]
    if not samples:
        return None

    values = sorted(v for v, _ in samples)
    hi = max(values)
    if hi <= 0:
        hi = 1.0

    width = hi / bins
    counts = [0.0] * bins
    for value, weight in samples:
        index = int(value / width) if width > 0 else 0
        counts[min(index, bins - 1)] += weight

    total = sum(counts) or 1.0
    bars = [
        {
            "lo": round(i * width, 4),
            "hi": round((i + 1) * width, 4),
            "seconds": round(counts[i], 2),
            "pct": round(100.0 * counts[i] / total, 3),
        }
        for i in range(bins)
    ]

    peak = max(range(bins), key=lambda i: counts[i])
    return {
        "bin_width_ms": width,
        "bins": bars,
        "total_s": total,
        "mode_lo_ms": bars[peak]["lo"],
        "mode_hi_ms": bars[peak]["hi"],
        "mean_ms": sum(v * w for v, w in samples) / total,
        "median_ms": _quantile(values, 0.50),
        "p25_ms": _quantile(values, 0.25),
        "p75_ms": _quantile(values, 0.75),
        "p95_ms": _quantile(values, 0.95),
        "max_ms": hi,
    }


def rolling_mean(
    t: Sequence[float], values: Sequence[float | None], window_s: float
) -> list[float | None]:
    """Trailing time-weighted moving average over ``window_s`` seconds.

    The running window sum is maintained incrementally, so it accumulates a
    little floating-point drift over thousands of samples. A mean can never
    fall outside the range of its inputs, so the result is clamped to the
    series minimum - without this a long stationary stretch can average to
    ``-1e-16`` and plot a hair below zero.
    """
    n = len(t)
    out: list[float | None] = [None] * n
    if n == 0:
        return out

    present = [v for v in values if v is not None]
    floor = min(present) if present else None

    dts = _deltas(t)
    j = 0
    total = 0.0
    total_w = 0.0

    for i in range(n):
        value = values[i]
        if value is not None:
            total += value * dts[i]
            total_w += dts[i]
        while j <= i and t[j] < t[i] - window_s:
            old = values[j]
            if old is not None:
                total -= old * dts[j]
                total_w -= dts[j]
            j += 1

        if total_w > 0:
            mean = total / total_w
            if floor is not None and mean < floor:
                mean = floor
            out[i] = mean

    return out


def splits(
    t: Sequence[float],
    distance: Sequence[float | None],
    speed: Sequence[float | None],
    hr: Sequence[float | None],
    altitude: Sequence[float | None],
    every_m: float = 1000.0,
) -> list[dict[str, Any]]:
    """Per-``every_m`` splits, interpolated onto the distance grid."""
    pairs = [(ti, d) for ti, d in zip(t, distance) if d is not None]
    if len(pairs) < 2:
        return []

    total = pairs[-1][1] - pairs[0][1]
    if total <= 0:
        return []

    base = pairs[0][1]
    result: list[dict[str, Any]] = []

    boundary = base + every_m
    index = 1
    while boundary <= pairs[-1][1] + 1e-6:
        while index < len(pairs) and pairs[index][1] < boundary:
            index += 1
        if index >= len(pairs):
            break

        d_prev, t_prev = pairs[index - 1][1], pairs[index - 1][0]
        d_cur, t_cur = pairs[index][1], pairs[index][0]
        span = d_cur - d_prev
        frac = (boundary - d_prev) / span if span > 0 else 0.0
        t_at = t_prev + (t_cur - t_prev) * frac

        start_d = boundary - every_m
        start_t = _interpolate_distance(pairs, start_d)
        if start_t is None:
            start_t = pairs[0][0]

        seg_speed = [
            v for ti, v in zip(t, speed)
            if start_t <= ti <= t_at and v is not None
        ]
        seg_hr = [v for ti, v in zip(t, hr) if start_t <= ti <= t_at and v is not None]
        seg_alt = [v for ti, v in zip(t, altitude) if start_t <= ti <= t_at and v is not None]

        duration = t_at - start_t
        result.append({
            "index": len(result) + 1,
            "distance_m": every_m,
            "duration_s": duration,
            "avg_speed_ms": (every_m / duration) if duration > 0 else None,
            "avg_hr": (sum(seg_hr) / len(seg_hr)) if seg_hr else None,
            "avg_cadence": None,
            "elevation_m": (sum(seg_alt) / len(seg_alt)) if seg_alt else None,
            "max_speed_ms": max(seg_speed) if seg_speed else None,
        })
        boundary += every_m

    return result


def _interpolate_distance(
    pairs: Sequence[tuple[float, float]], target: float
) -> float | None:
    """Time at which the given cumulative distance was reached."""
    if target <= pairs[0][1]:
        return pairs[0][0]
    for i in range(1, len(pairs)):
        if pairs[i][1] >= target:
            d_prev, t_prev = pairs[i - 1][1], pairs[i - 1][0]
            d_cur, t_cur = pairs[i][1], pairs[i][0]
            span = d_cur - d_prev
            frac = (target - d_prev) / span if span > 0 else 0.0
            return t_prev + (t_cur - t_prev) * frac
    return None


def lttb_indices(
    xs: Sequence[float], ys: Sequence[float], n_out: int
) -> list[int]:
    """Largest-Triangle-Three-Buckets downsampling.

    Returns the indices to keep, preserving visual shape (including peaks)
    far better than plain decimation.
    """
    n = len(xs)
    if n_out >= n or n_out < 3:
        return list(range(n))

    sampled = [0]
    every = (n - 2) / (n_out - 2)
    a = 0

    for i in range(n_out - 2):
        avg_start = int(math.floor((i + 1) * every)) + 1
        avg_end = min(int(math.floor((i + 2) * every)) + 1, n)
        count = avg_end - avg_start
        if count > 0:
            avg_x = sum(xs[avg_start:avg_end]) / count
            avg_y = sum(ys[avg_start:avg_end]) / count
        else:
            fallback = min(avg_start, n - 1)
            avg_x, avg_y = xs[fallback], ys[fallback]

        range_start = int(math.floor(i * every)) + 1
        range_end = int(math.floor((i + 1) * every)) + 1
        ax, ay = xs[a], ys[a]

        best_area = -1.0
        best = range_start
        for j in range(range_start, range_end):
            area = abs(
                (ax - avg_x) * (ys[j] - ay) - (ax - xs[j]) * (avg_y - ay)
            )
            if area > best_area:
                best_area = area
                best = j

        sampled.append(best)
        a = best

    sampled.append(n - 1)
    return sampled


def _chart_indices(t: Sequence[float], speed: Sequence[float | None], limit: int) -> list[int]:
    """Pick indices for charting, keyed on speed (falling back to time)."""
    n = len(t)
    if n <= limit:
        return list(range(n))
    usable = any(v is not None for v in speed)
    ys = [v if v is not None else 0.0 for v in speed] if usable else list(t)
    xs = list(t)
    return lttb_indices(xs, ys, limit)


def build_report(activity: dict[str, Any], max_points: int = MAX_CHART_POINTS) -> dict[str, Any]:
    """Everything the report page needs, in one payload."""
    series = activity["series"]
    t = series["t"]
    speed = series["speed"]

    keep = _chart_indices(t, speed, max_points)
    trace_t = [t[i] for i in keep]
    trace_speed = [speed[i] for i in keep]
    rolling = rolling_mean(t, speed, ROLLING_WINDOW_S)
    trace_rolling = [rolling[i] for i in keep]
    trace_hr = [series["hr"][i] for i in keep]
    trace_cadence = [series["cadence"][i] for i in keep]
    trace_alt = [series["altitude"][i] for i in keep]

    track = [
        [series["lon"][i], series["lat"][i]]
        for i in keep
        if series["lat"][i] is not None and series["lon"][i] is not None
    ]

    summary = summarize(activity)

    return {
        "version": REPORT_VERSION,
        "meta": activity["meta"],
        "is_paddling": is_paddling(activity["meta"].get("sport")),
        "summary": summary,
        "histogram": speed_histogram(t, speed),
        "trace": {
            "t": trace_t,
            "speed": trace_speed,
            "rolling": trace_rolling,
            "hr": trace_hr,
            "cadence": trace_cadence,
            "altitude": trace_alt,
            "window_s": ROLLING_WINDOW_S,
        },
        "splits": splits(t, series["distance"], speed, series["hr"], series["altitude"]),
        "track": track,
        "points_total": len(t),
        "points_charted": len(keep),
    }
