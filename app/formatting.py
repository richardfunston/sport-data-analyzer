"""Human-readable formatting helpers, shared by templates and the JSON API."""

from __future__ import annotations

import datetime as dt

PLACEHOLDER = "—"


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
    """``Sun 17 Feb 2019, 06:26`` from an ISO-8601 string."""
    if not iso:
        return PLACEHOLDER
    try:
        when = dt.datetime.fromisoformat(iso)
    except ValueError:
        return PLACEHOLDER
    return when.strftime("%a %d %b %Y, %H:%M")


def short_date(iso: str | None) -> str:
    if not iso:
        return PLACEHOLDER
    try:
        when = dt.datetime.fromisoformat(iso)
    except ValueError:
        return PLACEHOLDER
    return when.strftime("%d %b %Y")


def number(value: float | None, digits: int = 0) -> str:
    if value is None:
        return PLACEHOLDER
    return f"{value:,.{digits}f}"


def register_filters(app) -> None:
    """Attach the formatters to a Flask app as Jinja filters."""
    app.jinja_env.filters.update(
        hms=hms,
        compact_duration=compact_duration,
        distance=distance,
        speed=speed,
        pace=pace,
        daytime=daytime,
        short_date=short_date,
        number=number,
    )
