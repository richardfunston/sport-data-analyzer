"""Unit tests for the derived statistics."""

from __future__ import annotations

import math

import pytest

from conftest import activity

from app.analysis import (
    elevation_gain,
    lttb_indices,
    rolling_mean,
    speed_histogram,
    splits,
    summarize,
)


# --------------------------------------------------------------------------
# rolling_mean
# --------------------------------------------------------------------------

def test_rolling_mean_of_constant_series_is_that_constant():
    t = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert rolling_mean(t, [3.0] * 5, 30.0) == [3.0] * 5


def test_rolling_mean_uses_a_trailing_window():
    # 1 Hz samples, 2 s window: each output averages the sample and the one
    # before it, once the window has filled. The final sample has no duration
    # of its own, so it contributes no weight.
    t = [0.0, 1.0, 2.0, 3.0]
    values = [0.0, 10.0, 0.0, 10.0]
    out = rolling_mean(t, values, 2.0)

    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(5.0)          # (0 + 10) / 2
    assert out[2] == pytest.approx(10.0 / 3.0)   # (0 + 10 + 0) / 3
    assert out[3] == pytest.approx(5.0)          # (10 + 0) / 2, oldest dropped


def test_rolling_mean_ignores_missing_samples():
    t = [0.0, 1.0, 2.0]
    out = rolling_mean(t, [4.0, None, 4.0], 30.0)
    assert out == [4.0, 4.0, 4.0]


def test_rolling_mean_never_dips_below_the_series_minimum():
    # A long stationary stretch drives the sliding window sum toward zero.
    # Floating-point drift must not push the average below the inputs.
    t = [float(i) for i in range(1000)]
    values = [3.0] * 400 + [0.0] * 600

    out = rolling_mean(t, values, 30.0)

    assert all(v is None or v >= 0.0 for v in out)
    assert out[-1] == pytest.approx(0.0)
    assert max(out) <= 3.0


def test_rolling_mean_of_all_missing_is_missing():
    assert rolling_mean([0.0, 1.0], [None, None], 10.0) == [None, None]


# --------------------------------------------------------------------------
# speed_histogram
# --------------------------------------------------------------------------

def test_speed_histogram_buckets_time_by_speed():
    t = [0.0, 1.0, 2.0, 3.0]
    speed = [0.0, 1.0, 2.0, 3.0]
    hist = speed_histogram(t, speed, bins=3)

    assert hist["bin_width_ms"] == pytest.approx(1.0)
    # One second of dwell per band; the final sample carries no duration.
    assert [round(b["seconds"], 3) for b in hist["bins"]] == [1.0, 1.0, 1.0]
    # Total dwell time equals the elapsed time of the session.
    assert sum(b["seconds"] for b in hist["bins"]) == pytest.approx(3.0)
    assert sum(b["pct"] for b in hist["bins"]) == pytest.approx(100.0, abs=0.01)


def test_speed_histogram_reports_distribution_stats():
    t = [float(i) for i in range(101)]
    speed = [float(i) for i in range(101)]  # 0 .. 100
    hist = speed_histogram(t, speed, bins=10)

    assert hist["median_ms"] == pytest.approx(50.0)
    assert hist["max_ms"] == pytest.approx(100.0)
    # Time-weighted mean over the 100 s that actually elapsed.
    assert hist["mean_ms"] == pytest.approx(49.5)


def test_speed_histogram_without_speed_data_is_none():
    assert speed_histogram([0.0, 1.0], [None, None]) is None


def test_speed_histogram_all_zero_speed_does_not_divide_by_zero():
    hist = speed_histogram([0.0, 1.0], [0.0, 0.0], bins=4)
    assert hist is not None
    assert sum(b["seconds"] for b in hist["bins"]) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# elevation_gain
# --------------------------------------------------------------------------

def test_elevation_gain_of_flat_ground_is_zero():
    ascent, descent = elevation_gain(list(range(30)), [12.0] * 30)
    assert ascent == 0.0
    assert descent == 0.0


def test_elevation_gain_counts_a_climb_and_a_drop():
    altitude = [0.0] * 10 + [20.0] * 10 + [0.0] * 10
    ascent, descent = elevation_gain(list(range(30)), altitude)
    assert ascent == pytest.approx(20.0, abs=1.5)
    assert descent == pytest.approx(20.0, abs=1.5)


def test_elevation_gain_ignores_gps_jitter():
    # +/- 0.3 m of noise on flat ground must not register as climbing.
    altitude = [10.0 + 0.3 * (i % 2) for i in range(50)]
    ascent, descent = elevation_gain(list(range(50)), altitude)
    assert ascent == 0.0
    assert descent == 0.0


def test_elevation_gain_needs_altitude_data():
    assert elevation_gain([0.0, 1.0], [None, None]) == (None, None)


# --------------------------------------------------------------------------
# lttb_indices
# --------------------------------------------------------------------------

def test_lttb_keeps_requested_count_and_endpoints():
    xs = [float(i) for i in range(1000)]
    ys = [math.sin(i / 10.0) for i in range(1000)]
    indices = lttb_indices(xs, ys, 100)

    assert len(indices) == 100
    assert indices[0] == 0
    assert indices[-1] == 999
    assert indices == sorted(indices)


def test_lttb_preserves_a_spike_better_than_decimation():
    xs = [float(i) for i in range(1000)]
    ys = [0.0] * 1000
    ys[500] = 100.0

    indices = lttb_indices(xs, ys, 50)
    assert 500 in indices, "the spike sample must survive downsampling"


def test_lttb_returns_everything_when_target_exceeds_input():
    xs = [0.0, 1.0, 2.0]
    assert lttb_indices(xs, [1.0, 2.0, 3.0], 10) == [0, 1, 2]


# --------------------------------------------------------------------------
# summarize
# --------------------------------------------------------------------------

def test_summarize_constant_speed():
    t = [float(i) for i in range(101)]
    speed = [2.0] * 101
    distance = [2.0 * i for i in range(101)]

    summary = summarize(activity(t, speed=speed, distance=distance))

    assert summary["distance_m"] == pytest.approx(200.0)
    assert summary["elapsed_s"] == pytest.approx(100.0)
    assert summary["moving_s"] == pytest.approx(100.0)
    assert summary["stopped_s"] == pytest.approx(0.0)
    assert summary["avg_moving_speed_ms"] == pytest.approx(2.0, rel=1e-3)
    assert summary["avg_speed_ms"] == pytest.approx(2.0, rel=1e-3)
    assert summary["max_speed_ms"] == pytest.approx(2.0)


def test_summarize_separates_moving_time_from_stopped_time():
    # 60 s at 2 m/s, then 40 s stationary.
    t = [float(i) for i in range(101)]
    speed = [2.0] * 60 + [0.0] * 41
    distance = [2.0 * min(i, 59) for i in range(101)]

    summary = summarize(activity(t, speed=speed, distance=distance))

    assert summary["stopped_s"] == pytest.approx(41.0, abs=1.0)
    assert summary["moving_s"] == pytest.approx(60.0, abs=1.0)
    assert summary["max_speed_ms"] == pytest.approx(2.0)


def test_summarize_does_not_count_auto_pause_gaps_as_moving():
    # 60 s of paddling, a ten-minute hole in the timestamps, then 60 s more.
    # Wahoo auto-pause produces exactly this shape.
    t = [float(i) for i in range(60)] + [float(600 + i) for i in range(60)]
    speed = [3.0] * 120

    summary = summarize(activity(t, speed=speed))

    assert summary["elapsed_s"] == pytest.approx(659.0)
    # The 541 s hole must not be counted as paddling.
    assert summary["moving_s"] == pytest.approx(121.0, abs=2.0)
    assert summary["stopped_s"] > 500.0


def test_summarize_derives_distance_when_the_device_omits_it():
    t = [float(i) for i in range(11)]
    speed = [3.0] * 11

    summary = summarize(activity(t, speed=speed))

    assert summary["distance_m"] == pytest.approx(30.0, rel=0.05)


def test_summarize_reports_missing_metrics_as_none():
    t = [float(i) for i in range(11)]
    summary = summarize(activity(t, speed=[1.0] * 11))

    assert summary["max_hr"] is None
    assert summary["has_hr"] is False
    assert summary["has_cadence"] is False
    assert summary["has_gps"] is False


def test_summarize_weights_average_heart_rate_by_time():
    # 10 s at 100 bpm, then 10 s at 160 bpm.
    t = [float(i) for i in range(21)]
    hr = [100.0] * 11 + [160.0] * 10
    summary = summarize(activity(t, speed=[2.0] * 21, hr=hr))

    assert summary["avg_hr"] == pytest.approx(130.0, abs=5.0)
    assert summary["max_hr"] == pytest.approx(160.0)


# --------------------------------------------------------------------------
# splits
# --------------------------------------------------------------------------

def test_splits_divide_the_session_by_kilometre():
    t = [float(i) for i in range(301)]          # 300 s
    speed = [10.0] * 301                        # 10 m/s -> 1 km every 100 s
    distance = [10.0 * i for i in range(301)]   # 3 km total

    result = splits(t, distance, speed, [150.0] * 301, [5.0] * 301, every_m=1000.0)

    assert len(result) == 3
    assert [s["index"] for s in result] == [1, 2, 3]
    for split in result:
        assert split["duration_s"] == pytest.approx(100.0, abs=1.0)
        assert split["avg_speed_ms"] == pytest.approx(10.0, rel=0.02)


def test_splits_empty_without_distance():
    assert splits([0.0, 1.0], [None, None], [1.0, 1.0], [None, None], [None, None]) == []
