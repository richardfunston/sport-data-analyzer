"""Persistence for uploaded activities: a SQLite index plus on-disk blobs.

Each activity gets a short id. Three things are written under the data
directory:

* ``uploads/<id>.fit``            - the original upload, byte for byte
* ``uploads/<id>.report.json.gz`` - the fully built report payload
* ``sport.db``                    - the index and summary row for listing
"""

from __future__ import annotations

import gzip
import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id            TEXT PRIMARY KEY,
    filename      TEXT    NOT NULL,
    sha256        TEXT    NOT NULL UNIQUE,
    uploaded_at   TEXT    NOT NULL,
    start_time    TEXT,
    sport         TEXT,
    sub_sport     TEXT,
    device        TEXT,
    duration_s    REAL,
    moving_s      REAL,
    distance_m    REAL,
    avg_speed_ms  REAL,
    max_speed_ms  REAL,
    avg_hr        REAL,
    max_hr        REAL,
    avg_cadence   REAL,
    max_cadence   REAL,
    ascent_m      REAL,
    descent_m     REAL,
    calories      REAL,
    sample_count  INTEGER,
    summary_json  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS activities_start_time ON activities (start_time DESC);
"""


class Store:
    """Filesystem + SQLite store for parsed activities."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.uploads = self.data_dir / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "sport.db"
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # -- internals ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def raw_path(self, activity_id: str) -> Path:
        return self.uploads / f"{activity_id}.fit"

    def report_path(self, activity_id: str) -> Path:
        return self.uploads / f"{activity_id}.report.json.gz"

    @staticmethod
    def summary_columns(report: dict[str, Any]) -> dict[str, Any]:
        """Indexed columns derived from a report payload.

        Shared by :meth:`save` and :meth:`save_report` so a regenerated
        report cannot drift out of step with its index row.
        """
        summary = report["summary"]
        meta = report["meta"]
        return {
            "start_time": meta.get("start_time"),
            "sport": meta.get("sport"),
            "sub_sport": meta.get("sub_sport"),
            "device": meta.get("device"),
            "duration_s": summary.get("elapsed_s"),
            "moving_s": summary.get("moving_s"),
            "distance_m": summary.get("distance_m"),
            "avg_speed_ms": summary.get("avg_moving_speed_ms"),
            "max_speed_ms": summary.get("max_speed_ms"),
            "avg_hr": summary.get("avg_hr"),
            "max_hr": summary.get("max_hr"),
            "avg_cadence": summary.get("avg_cadence"),
            "max_cadence": summary.get("max_cadence"),
            "ascent_m": summary.get("ascent_m"),
            "descent_m": summary.get("descent_m"),
            "calories": summary.get("calories"),
            "sample_count": meta.get("sample_count"),
            "summary_json": json.dumps(summary, separators=(",", ":")),
        }

    def _new_id(self) -> str:
        for _ in range(50):
            candidate = secrets.token_hex(4)
            if not self.raw_path(candidate).exists():
                return candidate
        raise RuntimeError("could not allocate a unique activity id")

    # -- writes ------------------------------------------------------------

    def save(self, filename: str, raw: bytes, report: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Store an activity.

        Returns ``(record, created)``. Re-uploading identical bytes returns
        the existing record with ``created=False``.
        """
        digest = hashlib.sha256(raw).hexdigest()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM activities WHERE sha256 = ?", (digest,)
            ).fetchone()
        if existing is not None:
            return dict(existing), False

        activity_id = self._new_id()
        self.raw_path(activity_id).write_bytes(raw)
        with gzip.open(self.report_path(activity_id), "wt", encoding="utf-8") as fh:
            json.dump(report, fh, separators=(",", ":"))

        row = {
            "id": activity_id,
            "filename": filename,
            "sha256": digest,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            **self.summary_columns(report),
        }

        columns = ", ".join(row)
        placeholders = ", ".join(f":{c}" for c in row)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO activities ({columns}) VALUES ({placeholders})", row
            )
            conn.commit()

        return dict(row), True

    def save_report(self, activity_id: str, report: dict[str, Any]) -> None:
        """Rewrite a stored report and refresh its index row.

        Used when the analysis version changes, so existing uploads pick up
        improved statistics without being re-uploaded.
        """
        with gzip.open(self.report_path(activity_id), "wt", encoding="utf-8") as fh:
            json.dump(report, fh, separators=(",", ":"))

        columns = self.summary_columns(report)
        assignments = ", ".join(f"{column} = :{column}" for column in columns)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE activities SET {assignments} WHERE id = :id",
                {**columns, "id": activity_id},
            )
            conn.commit()

    def delete(self, activity_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
            conn.commit()
        self.raw_path(activity_id).unlink(missing_ok=True)
        self.report_path(activity_id).unlink(missing_ok=True)
        return cursor.rowcount > 0

    # -- reads -------------------------------------------------------------

    def get(self, activity_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM activities WHERE id = ?", (activity_id,)
            ).fetchone()
        return dict(row) if row else None

    def report(self, activity_id: str) -> dict[str, Any] | None:
        path = self.report_path(activity_id)
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM activities ORDER BY COALESCE(start_time, uploaded_at) DESC"
                " LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0])
