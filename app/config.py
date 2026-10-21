"""Runtime configuration, overridable by environment variable."""

from __future__ import annotations

import os
from pathlib import Path

#: Where uploaded .fit files and the SQLite index live.
DEFAULT_DATA_DIR = "/var/lib/sport-data-analyzer"


class Config:
    DATA_DIR = Path(os.environ.get("SDA_DATA_DIR", DEFAULT_DATA_DIR))

    #: URL prefix the app is mounted under (set when behind a path-mounting
    #: reverse proxy such as Caddy's ``handle_path``).
    BASE_PATH = os.environ.get("SDA_BASE_PATH", "").rstrip("/")

    #: Reject uploads larger than this. Wahoo files are well under 5 MB.
    MAX_CONTENT_LENGTH = int(os.environ.get("SDA_MAX_UPLOAD_BYTES", 64 * 1024 * 1024))

    #: Session signing key. Deliberately has no default: a key committed to the
    #: source is public, so anyone who reads the repository could forge a login
    #: cookie. ``create_app`` falls back to a generated, persisted key.
    SDA_SECRET_KEY = os.environ.get("SDA_SECRET_KEY")

    JSON_SORT_KEYS = False
