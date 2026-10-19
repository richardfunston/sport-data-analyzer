"""Tests for the FIT decoder."""

from __future__ import annotations

import pytest

from app.fitio import SERIES_KEYS, FitError, parse_fit
from app.profiles import is_paddling, sport_name, sub_sport_name, title


def test_parses_a_canoeing_session(canoe_fit):
    activity = parse_fit(canoe_fit)
    meta = activity["meta"]

    assert meta["sport"] == "canoeing"
    assert meta["manufacturer"] == "wahoo_fitness"
    assert meta["sample_count"] == 1560
    assert meta["start_time"].startswith("2024-06-15T08:00:00")


def test_every_series_column_is_the_same_length(canoe_fit):
    series = parse_fit(canoe_fit)["series"]
    lengths = {key: len(values) for key, values in series.items()}
    assert len(set(lengths.values())) == 1, lengths
    assert set(series) == set(SERIES_KEYS) | {"t"}


def test_timestamps_start_at_zero_and_increase(canoe_fit):
    t = parse_fit(canoe_fit)["series"]["t"]
    assert t[0] == 0.0
    assert t == sorted(t)
    assert t[-1] == pytest.approx(1559.0)


def test_coordinates_are_converted_to_degrees(canoe_fit):
    series = parse_fit(canoe_fit)["series"]
    lats = [v for v in series["lat"] if v is not None]
    lons = [v for v in series["lon"] if v is not None]

    assert lats and lons
    assert all(-90.0 <= v <= 90.0 for v in lats)
    assert all(-180.0 <= v <= 180.0 for v in lons)
    assert 51.0 < lats[0] < 52.0     # fixture is near 51.75 N
    assert -2.0 < lons[0] < -1.0


def test_distance_is_monotonic_and_matches_the_simulated_total(canoe_fit):
    distance = [v for v in parse_fit(canoe_fit)["series"]["distance"] if v is not None]
    assert distance == sorted(distance)
    assert distance[-1] == pytest.approx(4964.42, rel=0.01)


def test_speed_stays_within_plausible_bounds(canoe_fit):
    speeds = [v for v in parse_fit(canoe_fit)["series"]["speed"] if v is not None]
    assert speeds
    assert min(speeds) >= 0.0
    assert max(speeds) < 6.0


def test_parses_a_real_device_recording(real_fit):
    """Regression fixture: a real Wahoo ELEMNT ride, 13,471 records."""
    activity = parse_fit(real_fit)
    meta = activity["meta"]

    assert meta["manufacturer"] == "wahoo_fitness"
    assert meta["sport"] == "cycling"
    assert meta["sample_count"] == 13471
    assert len(activity["series"]["t"]) == meta["sample_count"]
    assert activity["series"]["t"] == sorted(activity["series"]["t"])


def test_real_recording_yields_sane_statistics(real_fit):
    """Regression fixture: a real Wahoo ELEMNT ride.

    Device-reported totals for this ride: 78,940 m, 13,471 s timer time,
    23,595 s elapsed, 5.86 m/s average, 10.954 m/s max, max HR 170.
    """
    from app.analysis import build_report

    report = build_report(parse_fit(real_fit))
    summary = report["summary"]

    assert summary["distance_m"] == pytest.approx(78940, rel=0.01)
    assert summary["elapsed_s"] == pytest.approx(23595, rel=0.01)
    assert summary["max_speed_ms"] == pytest.approx(10.95, rel=0.01)
    assert summary["max_hr"] == 170

    # The ride has 48 auto-pause holes in the timestamps, the largest 42
    # minutes long. Moving time must land near the device's timer time
    # (13,471 s), not anywhere near the 6h33m wall clock.
    assert 12000 < summary["moving_s"] < 13600
    assert summary["stopped_s"] == pytest.approx(
        summary["elapsed_s"] - summary["moving_s"]
    )

    assert summary["has_hr"] is True
    assert summary["has_gps"] is True
    assert report["histogram"] is not None
    assert len(report["splits"]) == 78, "a 78.9 km ride must split into 78 km"


def test_real_recording_track_is_geographically_sane(real_fit):
    from app.analysis import build_report

    track = build_report(parse_fit(real_fit))["track"]
    assert len(track) > 100

    lons = [point[0] for point in track]
    lats = [point[1] for point in track]
    assert all(-180.0 <= v <= 180.0 for v in lons)
    assert all(-90.0 <= v <= 90.0 for v in lats)


# --------------------------------------------------------------------------
# rejection paths
# --------------------------------------------------------------------------

def test_rejects_random_bytes():
    with pytest.raises(FitError):
        parse_fit(b"this is definitely not a fit file" * 40)


def test_rejects_empty_input():
    with pytest.raises(FitError):
        parse_fit(b"")


def test_rejects_a_truncated_file(canoe_fit):
    with pytest.raises(FitError):
        parse_fit(canoe_fit[: len(canoe_fit) // 3])


# --------------------------------------------------------------------------
# profile tables
# --------------------------------------------------------------------------

def test_sport_enum_integers_resolve_to_names():
    # fitdecode's bundled profile predates these values and returns the raw int.
    assert sport_name(88) == "canoeing"
    assert sport_name(41) == "kayaking"
    assert sport_name(19) == "paddling"


def test_sport_names_pass_through_unchanged():
    assert sport_name("cycling") == "cycling"
    assert sub_sport_name("road") == "road"


def test_unknown_enum_values_are_none_not_an_exception():
    assert sport_name(9999) is None
    assert sport_name(None) is None
    assert sub_sport_name(None) is None


def test_is_paddling_recognises_canoe_sports():
    assert is_paddling("canoeing")
    assert is_paddling("kayaking")
    assert not is_paddling("cycling")
    assert not is_paddling(None)


def test_title_formats_snake_case_sports():
    assert title("stand_up_paddleboarding") == "Stand Up Paddleboarding"
    assert title(None) == ""
