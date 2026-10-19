"""Tests for the template/Jinja formatting helpers."""

from __future__ import annotations

import pytest

from app.formatting import (
    compact_duration,
    distance,
    hms,
    pace,
    speed,
    window_label,
)


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (30, "30 s"),
        (45, "45 s"),
        (59, "59 s"),
        (60, "1 min"),
        (120, "2 min"),
        (300, "5 min"),
        (600, "10 min"),
        (90, "1.5 min"),
    ],
)
def test_window_label(seconds, expected):
    assert window_label(seconds) == expected


def test_window_label_handles_missing_values():
    assert window_label(None) == "—"


def test_duration_formatting():
    assert hms(0) == "0:00"
    assert hms(59) == "0:59"
    assert hms(600) == "10:00"
    assert hms(3661) == "1:01:01"
    assert hms(None) == "—"

    assert compact_duration(600) == "10m"
    assert compact_duration(5400) == "1h 30m"
    assert compact_duration(None) == "—"


def test_distance_and_speed_formatting():
    assert distance(1500) == "1.50 km"
    assert distance(1500, "mi") == "0.93 mi"
    assert distance(None) == "—"

    assert speed(3.0) == "10.8 km/h"
    assert speed(3.0, "mph") == "6.7 mph"
    assert speed(None) == "—"


def test_pace_formatting():
    # 3.333 m/s is 5:00 per kilometre.
    assert pace(1000 / 300) == "5:00 /km"
    assert pace(None) == "—"
    assert pace(0.0) == "—"
