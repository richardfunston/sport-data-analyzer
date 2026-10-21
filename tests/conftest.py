"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import create_app
from app.config import Config
from app.fitio import SERIES_KEYS

FIXTURES = Path(__file__).parent / "fixtures"

#: Synthetic canoeing session produced by tools/make_sample_fit.py.
CANOE_FIT = FIXTURES / "canoe-session.fit"

#: A real Wahoo ELEMNT recording, used as a real-world regression fixture.
REAL_FIT = FIXTURES / "real-wahoo-elemnt.fit"


@pytest.fixture(scope="session")
def canoe_fit() -> bytes:
    if not CANOE_FIT.exists():
        pytest.skip("canoe fixture missing; run tools/make_sample_fit.py")
    return CANOE_FIT.read_bytes()


@pytest.fixture(scope="session")
def real_fit() -> bytes:
    if not REAL_FIT.exists():
        pytest.skip("real-world fixture missing")
    return REAL_FIT.read_bytes()


@pytest.fixture()
def app(tmp_path):
    """A Flask app wired to a throwaway data directory."""

    class TestConfig(Config):
        DATA_DIR = tmp_path / "data"
        TESTING = True

    application = create_app(TestConfig)
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def store(app):
    """The store the app is actually using, over its throwaway data directory.

    Deliberately read off the app rather than constructed here: a second Store
    rooted somewhere else would let tests pass while the app used another
    database.
    """
    return app.extensions["store"]


def minimal_report(**meta):
    """A report payload with the fields the index depends on."""
    from app.analysis import REPORT_VERSION

    return {
        "version": REPORT_VERSION,
        "meta": {
            "start_time": "2024-06-15T08:00:00+00:00",
            "sport": "canoeing",
            **meta,
        },
        "summary": {"elapsed_s": 100.0, "distance_m": 500.0},
        "series": [],
    }


@pytest.fixture()
def saved_activity(store):
    """A stored activity, needing no .fit file."""
    record, _ = store.save("canoe.fit", b"\x00fit", minimal_report())
    return record["id"]


def activity(t, sport="canoeing", sub_sport=None, **series):
    """Build a minimal activity dict for the analysis functions."""
    columns = {key: [None] * len(t) for key in SERIES_KEYS}
    columns["t"] = list(t)
    for key, values in series.items():
        columns[key] = list(values)
    return {
        "meta": {
            "start_time": "2024-01-01T00:00:00+00:00",
            "sport": sport,
            "sub_sport": sub_sport,
        },
        "series": columns,
        "session": {},
    }
