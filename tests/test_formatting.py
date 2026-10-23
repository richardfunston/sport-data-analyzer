"""Tests for the template/Jinja formatting helpers."""

from __future__ import annotations

import pytest

from app.formatting import (
    DEFAULT_DISPLAY_TZ,
    compact_duration,
    daytime,
    distance,
    hms,
    pace,
    set_display_tz,
    short_date,
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


# -- timestamps render in the display timezone ------------------------------
#
# FIT files record UTC and the store keeps it that way, deliberately. These
# cover the single place the clock a person actually reads is produced. The bug
# they guard against was silent: the stored value was always correct, only the
# rendering ignored the offset, so nothing looked corrupt and nothing failed.


def test_daytime_renders_in_the_display_timezone():
    # 22:04 UTC on 30 May is 08:04 the next morning in Sydney, AEST (+10).
    assert daytime("2026-05-30T22:04:12+00:00") == "Sun 31 May 2026, 08:04 AEST"


def test_daytime_follows_daylight_saving():
    # Sydney is AEDT (+11) from October to April, so a January instant shifts by
    # a different amount. A hardcoded +10 would pass the test above and still be
    # an hour wrong for half the year.
    assert daytime("2026-01-15T20:00:00+00:00") == "Fri 16 Jan 2026, 07:00 AEDT"


def test_short_date_uses_the_local_calendar_day():
    # The UTC date is 30 May; the Sydney date is 31 May. Rendering the UTC date
    # is the off-by-one-day half of the same bug, and it silently mislabels
    # evening sessions as the previous day.
    assert short_date("2026-05-30T22:04:12+00:00") == "31 May 2026"
    assert short_date("2026-07-25T23:46:00+00:00") == "26 Jul 2026"


def test_naive_timestamps_are_read_as_utc():
    # FIT timestamps are UTC by definition. Treating a naive value as the
    # server's local time would make the rendered result depend on where the
    # process happens to run.
    assert daytime("2026-05-30T22:04:12") == "Sun 31 May 2026, 08:04 AEST"


@pytest.mark.parametrize("value", [None, "", "not-a-date", "2026-13-45T99:99:99"])
def test_time_formatters_survive_missing_and_broken_input(value):
    assert daytime(value) == "\u2014"
    assert short_date(value) == "\u2014"


def test_the_display_timezone_is_configurable():
    # The zone is a setting, not a constant: the same code has to serve a report
    # rendered anywhere.
    set_display_tz("UTC")
    try:
        assert daytime("2026-05-30T22:04:12+00:00") == "Sat 30 May 2026, 22:04 UTC"
    finally:
        set_display_tz(DEFAULT_DISPLAY_TZ)


def test_an_unknown_timezone_degrades_instead_of_breaking_the_page():
    # A typo in the setting must not take the whole index down.
    set_display_tz("Mars/Olympus_Mons")
    try:
        assert daytime("2026-05-30T22:04:12+00:00") == "Sun 31 May 2026, 08:04 AEST"
    finally:
        set_display_tz(DEFAULT_DISPLAY_TZ)
