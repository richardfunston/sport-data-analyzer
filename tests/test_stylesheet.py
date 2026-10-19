"""Tests for the stylesheet invariants that are easy to break silently."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css"
APP_DIR = CSS_PATH.parents[2]          # .../app
TEMPLATE_PATH = APP_DIR / "templates" / "activity.html"


@pytest.fixture(scope="module")
def css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def test_stylesheet_exists(css):
    assert len(css) > 1000


def test_hidden_attribute_is_forced_to_win(css):
    """The `hidden` attribute must beat component display rules.

    The UA stylesheet's `[hidden] { display: none }` loses to any author
    `display:` declaration, so `.track { display: block }` made the offline map
    fallback permanently visible - an empty panel below the route that looked
    like a broken tile. An explicit override is the only thing that holds.
    """
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css), (
        "the stylesheet must force [hidden] { display: none !important }"
    )


def test_every_element_the_page_toggles_ships_hidden():
    """These are revealed by clearing `hidden`, so the override above matters."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for name in ("track", "map", "basemap-control", "hr-card", "elev-card",
                 "splits-card", "track-card"):
        assert f'id="{name}"' in template, name


def test_map_and_fallback_share_one_height_knob(css):
    """The SVG fallback and the Leaflet map must not disagree on height."""
    assert css.count("var(--map-height)") >= 2, "map and .track should share the knob"
    assert "--map-height:" in css


def test_the_map_is_taller_on_a_phone_than_it_used_to_be(css):
    desktop = re.search(r"--map-height:\s*(\d+)px", css)
    assert desktop, "no default map height"
    assert int(desktop.group(1)) >= 460, "map should be comfortably tall"

    mobile = re.search(
        r"@media \(max-width: 520px\)[^}]*\{[^}]*--map-height:\s*(\d+)px", css, re.S
    )
    assert mobile, "no mobile map height"
    assert int(mobile.group(1)) >= 400, "phone map should be taller too"


def test_shipped_stylesheet_carries_the_override(app):
    response = app.test_client().get("/static/css/style.css")
    assert response.status_code == 200
    assert b"[hidden]" in response.data

