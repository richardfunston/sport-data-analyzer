"""Human-readable formatting helpers, shared by templates and the JSON API.

Timestamps are the interesting ones. FIT files record UTC, the store keeps UTC,
and this module is the *only* place that turns UTC into the clock a person
reads. That conversion happens here rather than in the browser because it has to
give the same answer everywhere: rendering it client-side made the index (server
rendered) and the report page (browser rendered) disagree about the same session.
"""

from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PLACEHOLDER = "—"

#: Zone timestamps are rendered in. Sydney, because that is where the sessions
#: are recorded and read.
DEFAULT_DISPLAY_TZ = "Australia/Sydney"

_tz_name = os.environ.get("SDA_DISPLAY_TZ") or DEFAULT_DISPLAY_TZ
_tz: ZoneInfo | None = None


def display_tz_name() -> str:
    """The configured display zone name, whether or not it resolves."""
    return _tz_name


def set_display_tz(name: str | None) -> None:
    """Pin the zone used to render timestamps.

    Called once at application startup from the config, and by tests. Kept in a
    module global because the Jinja filters are plain functions with no access to
    the app.
    """
    global _tz_name, _tz
    _tz_name = name or DEFAULT_DISPLAY_TZ
    _tz = None


def display_zone() -> ZoneInfo:
    """The display zone, falling back rather than raising.

    A bad setting must not take the index down, so an unresolvable name degrades
    to the default instead of propagating ``ZoneInfoNotFoundError`` out of a
    template render.
    """
    global _tz
    if _tz is None:
        try:
            _tz = ZoneInfo(_tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            _tz = ZoneInfo(DEFAULT_DISPLAY_TZ)
    return _tz


def to_display(iso: str | None) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp and express it in the display zone.

    A naive string is read as UTC rather than as the server's local time: FIT
    timestamps are UTC by definition, and treating a naive value as local would
    make the rendered clock depend on where the process runs.
    """
    if not iso:
        return None
    try:
        when = dt.datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(display_zone())


def hms(seconds: float | None) -> str:
    """``1:04:32`` / ``4:32``."""
    if seconds is None:
        return PLACEHOLDER
    total = int(round(seconds))
    hours, rem = divmod(abs(total), 3600)
    minutes, secs = divmod(rem, 60)
    sign = "-" if total < 0 else ""
    if hours:
        return f"{sign}{hours}:{minutes:02d}:{secs:02d}"
    return f"{sign}{minutes}:{secs:02d}"


def compact_duration(seconds: float | None) -> str:
    """``1h 04m`` / ``42m``."""
    if seconds is None:
        return PLACEHOLDER
    total = int(round(seconds))
    hours, rem = divmod(abs(total), 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def distance(metres: float | None, unit: str = "km") -> str:
    if metres is None:
        return PLACEHOLDER
    if unit == "mi":
        return f"{metres / 1609.344:.2f} mi"
    if unit == "m":
        return f"{metres:.0f} m"
    return f"{metres / 1000.0:.2f} km"


def speed(ms: float | None, unit: str = "kmh") -> str:
    if ms is None:
        return PLACEHOLDER
    if unit == "mph":
        return f"{ms * 2.236936:.1f} mph"
    return f"{ms * 3.6:.1f} km/h"


def pace(ms: float | None) -> str:
    """Paddling pace in minutes per kilometre."""
    if ms is None or ms <= 0.05:
        return PLACEHOLDER
    seconds_per_km = 1000.0 / ms
    minutes, secs = divmod(int(round(seconds_per_km)), 60)
    return f"{minutes}:{secs:02d} /km"


def daytime(iso: str | None) -> str:
    """``Sun 31 May 2026, 08:04 AEST`` from a UTC ISO-8601 string.

    The zone abbreviation is part of the output on purpose: an unqualified time
    on a page that is rendered in a different zone from the one it was recorded
    in is exactly the ambiguity this function exists to remove.
    """
    when = to_display(iso)
    if when is None:
        return PLACEHOLDER
    return when.strftime("%a %d %b %Y, %H:%M %Z")


def short_date(iso: str | None) -> str:
    when = to_display(iso)
    if when is None:
        return PLACEHOLDER
    return when.strftime("%d %b %Y")


def number(value: float | None, digits: int = 0) -> str:
    if value is None:
        return PLACEHOLDER
    return f"{value:,.{digits}f}"


def window_label(seconds: float | None) -> str:
    """``30`` -> ``30 s``, ``60`` -> ``1 min``, ``600`` -> ``10 min``."""
    if seconds is None:
        return PLACEHOLDER
    value = int(round(seconds))
    if value < 60:
        return f"{value} s"
    minutes = value / 60.0
    if minutes == int(minutes):
        return f"{int(minutes)} min"
    return f"{minutes:.1f} min"


def register_filters(app) -> None:
    """Attach the formatters to a Flask app as Jinja filters."""
    set_display_tz(app.config.get("DISPLAY_TZ"))
    app.jinja_env.filters.update(
        hms=hms,
        compact_duration=compact_duration,
        distance=distance,
        speed=speed,
        pace=pace,
        daytime=daytime,
        short_date=short_date,
        number=number,
        window_label=window_label,
    )
