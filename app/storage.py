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

from .auth import check_password, hash_password

__all__ = ["DuplicateEmail", "Store"]


class DuplicateEmail(Exception):
    """Raised when a paddler is created with an email that already exists."""

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

-- A paddler is an account. A session is a shared record, so a canoe with two
-- paddlers is one activity linked twice: hence the join table rather than an
-- owner column.
--
-- email is COLLATE NOCASE because SQLite's default collation for TEXT is
-- BINARY, so a plain UNIQUE would accept RPF@ beside rpf@ and split one person
-- into two accounts. Python lowercases as well; this makes the database enforce
-- it whatever the write path.
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    session_epoch INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    modified_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_paddlers (
    activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    PRIMARY KEY (activity_id, user_id)
);
CREATE INDEX IF NOT EXISTS activity_paddlers_user ON activity_paddlers (user_id);
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

    # -- paddlers ----------------------------------------------------------

    @staticmethod
    def _normalise_email(email: str) -> str:
        """One place to trim and case-fold, so no write path can skip it."""
        return email.strip().lower()

    def _new_user_id(self) -> str:
        with self._connect() as conn:
            for _ in range(50):
                candidate = secrets.token_hex(4)
                if (
                    conn.execute(
                        "SELECT 1 FROM users WHERE id = ?", (candidate,)
                    ).fetchone()
                    is None
                ):
                    return candidate
        raise RuntimeError("could not allocate a unique paddler id")

    def create_paddler(
        self,
        email: str,
        name: str,
        password: str,
        *,
        is_admin: bool = False,
        is_active: bool = True,
    ) -> dict[str, Any]:
        """Create a paddler. Raises :class:`DuplicateEmail` on a repeat email."""
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": self._new_user_id(),
            "email": self._normalise_email(email),
            "name": name,
            "password_hash": hash_password(password),
            "is_admin": 1 if is_admin else 0,
            "is_active": 1 if is_active else 0,
            "session_epoch": 1,
            "created_at": now,
            "modified_at": now,
        }
        columns = ", ".join(row)
        placeholders = ", ".join(f":{c}" for c in row)
        try:
            with self._connect() as conn:
                conn.execute(
                    f"INSERT INTO users ({columns}) VALUES ({placeholders})", row
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateEmail(row["email"]) from exc
        return dict(row)

    def get_paddler(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_paddler_by_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (self._normalise_email(email),),
            ).fetchone()
        return dict(row) if row else None

    def verify_paddler(self, email: str, password: str) -> dict[str, Any] | None:
        """The paddler whose email and password match, if any.

        A deactivated paddler never verifies, so a disabled account cannot sign
        back in with a password it already knew.
        """
        user = self.get_paddler_by_email(email)
        if user is None or not user["is_active"]:
            return None
        if not check_password(password, user["password_hash"]):
            return None
        return user

    def list_paddlers(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY is_active DESC, name, email"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_paddler(
        self,
        user_id: str,
        *,
        name: str | None = None,
        is_admin: bool | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any] | None:
        """Rename, promote or (de)activate. ``None`` for an unknown id."""
        if self.get_paddler(user_id) is None:
            return None

        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if is_admin is not None:
            fields["is_admin"] = 1 if is_admin else 0
        if is_active is not None:
            fields["is_active"] = 1 if is_active else 0

        if fields:
            fields["modified_at"] = datetime.now(timezone.utc).isoformat()
            assignments = ", ".join(f"{column} = :{column}" for column in fields)
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE users SET {assignments} WHERE id = :id",
                    {**fields, "id": user_id},
                )
                conn.commit()
        return self.get_paddler(user_id)

    def set_password(self, user_id: str, password: str) -> bool:
        """Set a new password and bump ``session_epoch``.

        The bump is what makes a password change evict everyone already signed
        in: every cookie issued before the change carries an older epoch.
        """
        if self.get_paddler(user_id) is None:
            return False
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, session_epoch = session_epoch + 1,"
                " modified_at = ? WHERE id = ?",
                (
                    hash_password(password),
                    datetime.now(timezone.utc).isoformat(),
                    user_id,
                ),
            )
            conn.commit()
        return True

    def session_epoch_for(self, user_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_epoch FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return int(row["session_epoch"]) if row else None

    def count_admins(self, *, active_only: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM users WHERE is_admin = 1"
        if active_only:
            sql += " AND is_active = 1"
        with self._connect() as conn:
            return int(conn.execute(sql).fetchone()[0])

    def first_admin(self) -> dict[str, Any] | None:
        """The account a migration should attribute unowned rows to."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE is_admin = 1 AND is_active = 1"
                " ORDER BY created_at, id LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def backfill_paddlers(self, user_id: str) -> int:
        """Link every activity that has no paddler at all to ``user_id``.

        Idempotent: a second run inserts nothing. Returns the number adopted.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO activity_paddlers (activity_id, user_id)"
                " SELECT id, ? FROM activities"
                " WHERE id NOT IN (SELECT activity_id FROM activity_paddlers)",
                (user_id,),
            )
            conn.commit()
        return cursor.rowcount

    # -- sharing -----------------------------------------------------------

    def link_paddler(self, activity_id: str, user_id: str) -> bool:
        """Link a paddler to a session. False when already linked."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO activity_paddlers (activity_id, user_id)"
                " VALUES (?, ?)",
                (activity_id, user_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def unlink_paddler(self, activity_id: str, user_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM activity_paddlers WHERE activity_id = ? AND user_id = ?",
                (activity_id, user_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def paddlers_for(self, activity_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT u.* FROM users u"
                " JOIN activity_paddlers ap ON ap.user_id = u.id"
                " WHERE ap.activity_id = ? ORDER BY u.name, u.id",
                (activity_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- reads scoped to one paddler ---------------------------------------

    _SCOPED_SELECT = (
        "SELECT a.* FROM activities a"
        " JOIN activity_paddlers ap ON ap.activity_id = a.id"
    )

    def list_for(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                self._SCOPED_SELECT
                + " WHERE ap.user_id = ?"
                " ORDER BY COALESCE(a.start_time, a.uploaded_at) DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_for(self, activity_id: str, user_id: str) -> dict[str, Any] | None:
        """The activity, but only if this paddler is linked to it.

        ``None`` for an unlinked paddler is deliberate: the routes turn it into
        a 404, so an outsider cannot confirm the id exists.
        """
        with self._connect() as conn:
            row = conn.execute(
                self._SCOPED_SELECT + " WHERE a.id = ? AND ap.user_id = ?",
                (activity_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def count_for(self, user_id: str) -> int:
        with self._connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM activity_paddlers WHERE user_id = ?",
                    (user_id,),
                ).fetchone()[0]
            )
