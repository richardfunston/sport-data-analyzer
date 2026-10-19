"""Tile providers offered as map backgrounds.

All of these are key-free, which is the point: the app is self-hosted on a LAN
and must not depend on an API key or a billing account. Each entry carries the
attribution its provider requires - that text is rendered on the map and must
stay with the layer.

To add Google Maps instead, it needs a key and a billing-enabled Cloud project;
the swap is confined to this module plus the map code in ``report.js``.
"""

from __future__ import annotations

from typing import Any

#: Ordered: the first entry is the one a report opens with.
BASEMAPS: list[dict[str, Any]] = [
    {
        "key": "streets",
        "label": "Streets",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": (
            '&copy; <a href="https://www.openstreetmap.org/copyright">'
            "OpenStreetMap</a> contributors"
        ),
        "max_zoom": 19,
        "subdomains": "abc",
    },
    {
        "key": "satellite",
        "label": "Satellite",
        "url": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        "attribution": "Imagery &copy; Esri, Maxar, Earthstar Geographics",
        "max_zoom": 19,
        "subdomains": "abc",
    },
    {
        "key": "terrain",
        "label": "Terrain",
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": (
            'Map data &copy; <a href="https://www.openstreetmap.org/copyright">'
            "OpenStreetMap</a> contributors, SRTM | style &copy; "
            '<a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)'
        ),
        "max_zoom": 17,
        "subdomains": "abc",
    },
    {
        "key": "dark",
        "label": "Dark",
        "url": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attribution": (
            '&copy; <a href="https://www.openstreetmap.org/copyright">'
            "OpenStreetMap</a> contributors &copy; "
            '<a href="https://carto.com/attributions">CARTO</a>'
        ),
        "max_zoom": 20,
        "subdomains": "abcd",
    },
]

DEFAULT_BASEMAP = BASEMAPS[0]["key"]


def basemap_keys() -> list[str]:
    return [entry["key"] for entry in BASEMAPS]
