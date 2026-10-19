"""Tests for the map background catalogue."""

from __future__ import annotations

from app.basemaps import BASEMAPS, DEFAULT_BASEMAP, basemap_keys


def test_a_default_basemap_exists_and_is_the_first_entry():
    assert DEFAULT_BASEMAP == BASEMAPS[0]["key"]
    assert DEFAULT_BASEMAP in basemap_keys()


def test_keys_are_unique():
    keys = basemap_keys()
    assert len(keys) == len(set(keys))


def test_every_entry_is_usable_as_a_leaflet_tile_layer():
    for entry in BASEMAPS:
        assert entry["key"] and entry["label"], entry
        for placeholder in ("{z}", "{x}", "{y}"):
            assert placeholder in entry["url"], (entry["key"], placeholder)
        assert entry["attribution"], entry["key"]
        assert isinstance(entry["max_zoom"], int) and entry["max_zoom"] > 0


def test_no_provider_needs_an_api_key():
    """The whole point: the app is self-hosted and must not need billing."""
    for entry in BASEMAPS:
        url = entry["url"].lower()
        assert "key=" not in url, entry["key"]
        assert "apikey" not in url, entry["key"]
        assert "token=" not in url, entry["key"]


def test_streets_and_satellite_are_offered():
    keys = basemap_keys()
    assert "streets" in keys
    assert "satellite" in keys


def test_providers_are_https():
    for entry in BASEMAPS:
        assert entry["url"].startswith("https://"), entry["key"]
