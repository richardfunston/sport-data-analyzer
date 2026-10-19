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


def activity(t, **series):
    """Build a minimal activity dict for the analysis functions."""
    columns = {key: [None] * len(t) for key in SERIES_KEYS}
    columns["t"] = list(t)
    for key, values in series.items():
        columns[key] = list(values)
    return {
        "meta": {"start_time": "2024-01-01T00:00:00+00:00", "sport": "canoeing"},
        "series": columns,
        "session": {},
    }
