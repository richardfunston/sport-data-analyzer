"""The member table and the per-session member links."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app import create_app
from app.config import Config
from app.storage import DuplicateEmail, LastAdminError, Store

from conftest import minimal_report


def test_create_and_fetch_member(store):
    user = store.create_member(
        "rpf@funston.com", "Richard Funston", "not-a-real-password", is_admin=True
    )
    assert user["email"] == "rpf@funston.com"
    assert user["name"] == "Richard Funston"
    assert user["is_admin"] == 1
    assert user["password_hash"] != "not-a-real-password"
    assert store.get_member_by_email("rpf@funston.com")["id"] == user["id"]
    assert store.get_member(user["id"])["name"] == "Richard Funston"


def test_created_member_is_active_and_not_admin_by_default(store):
    user = store.create_member("a@funston.com", "A", "pw")
    assert user["is_active"] == 1
    assert user["is_admin"] == 0


def test_email_is_lowercased_and_trimmed(store):
    user = store.create_member("  RPF@Funston.COM  ", "Richard", "pw")
    assert user["email"] == "rpf@funston.com"


def test_duplicate_email_rejected(store):
    store.create_member("rpf@funston.com", "Richard", "pw")
    with pytest.raises(DuplicateEmail):
        store.create_member("RPF@funston.com", "Someone Else", "pw")


def test_verify_member(store):
    store.create_member("rpf@funston.com", "Richard", "not-a-real-password")
    assert store.verify_member("rpf@funston.com", "not-a-real-password") is not None
    assert store.verify_member(" RPF@Funston.com ", "not-a-real-password") is not None
    assert store.verify_member("rpf@funston.com", "wrong") is None
    assert store.verify_member("nobody@funston.com", "not-a-real-password") is None


def test_verify_rejects_a_deactivated_member(store):
    user = store.create_member("rpf@funston.com", "Richard", "pw")
    store.update_member(user["id"], is_active=False)
    assert store.verify_member("rpf@funston.com", "pw") is None


def test_set_password_bumps_session_epoch(store):
    user = store.create_member("rpf@funston.com", "Richard", "old")
    before = store.session_epoch_for(user["id"])
    assert store.set_password(user["id"], "new") is True
    assert store.session_epoch_for(user["id"]) == before + 1
    assert store.verify_member("rpf@funston.com", "new") is not None
    assert store.verify_member("rpf@funston.com", "old") is None


def test_session_epoch_for_unknown_member_is_none(store):
    assert store.session_epoch_for("nope") is None


def test_set_password_for_unknown_member_is_false(store):
    assert store.set_password("nope", "pw") is False


def test_update_member_returns_none_for_unknown_id(store):
    assert store.update_member("nope", name="X") is None


def test_update_member_renames_and_promotes(store):
    user = store.create_member("a@funston.com", "A", "pw")
    updated = store.update_member(user["id"], name="Ann", is_admin=True)
    assert updated["name"] == "Ann"
    assert updated["is_admin"] == 1
    assert store.get_member(user["id"])["name"] == "Ann"


def test_deactivating_does_not_delete_the_row(store):
    user = store.create_member("rpf@funston.com", "Richard", "pw")
    updated = store.update_member(user["id"], is_active=False)
    assert updated["is_active"] == 0
    assert store.get_member(user["id"]) is not None


def test_link_and_visibility(store, saved_activity):
    a = store.create_member("a@funston.com", "A", "pw")
    b = store.create_member("b@funston.com", "B", "pw")
    assert store.link_member(saved_activity, a["id"]) is True
    assert store.link_member(saved_activity, a["id"]) is False  # already linked
    assert [p["id"] for p in store.members_for(saved_activity)] == [a["id"]]
    assert store.get_for(saved_activity, a["id"]) is not None
    assert store.get_for(saved_activity, b["id"]) is None
    assert [r["id"] for r in store.list_for(a["id"])] == [saved_activity]
    assert store.list_for(b["id"]) == []
    assert store.count_for(a["id"]) == 1
    assert store.count_for(b["id"]) == 0
    assert store.unlink_member(saved_activity, a["id"]) is True
    assert store.unlink_member(saved_activity, a["id"]) is False
    assert store.get_for(saved_activity, a["id"]) is None


def test_a_session_can_have_several_members(store, saved_activity):
    """A two-member canoe is one session linked twice."""
    a = store.create_member("a@funston.com", "A", "pw")
    b = store.create_member("b@funston.com", "B", "pw")
    store.link_member(saved_activity, a["id"])
    store.link_member(saved_activity, b["id"])
    assert {p["id"] for p in store.members_for(saved_activity)} == {a["id"], b["id"]}
    assert store.count_for(a["id"]) == 1
    assert store.count_for(b["id"]) == 1


def test_backfill_links_only_unlinked_activities(store):
    """An activity with no link at all is adopted; a linked one is left alone."""
    orphan, _ = store.save("orphan.fit", b"\x00orphan", minimal_report())
    mine, _ = store.save("mine.fit", b"\x00mine", minimal_report(start_time="2024-07-01T00:00:00+00:00"))
    richard = store.create_member("rpf@funston.com", "Richard", "pw", is_admin=True)
    other = store.create_member("o@funston.com", "O", "pw")
    store.link_member(mine["id"], other["id"])

    assert store.backfill_members(richard["id"]) == 1
    assert store.get_for(orphan["id"], richard["id"]) is not None
    assert store.get_for(mine["id"], richard["id"]) is None
    assert store.get_for(mine["id"], other["id"]) is not None
    # A second run finds nothing left to adopt.
    assert store.backfill_members(richard["id"]) == 0
    assert store.backfill_members(other["id"]) == 0


def test_deleting_an_activity_cascades_every_link(store):
    """A shared session's links must all go with it, not just the first."""
    a = store.create_member("a@funston.com", "A", "pw")
    b = store.create_member("b@funston.com", "B", "pw")
    record, _ = store.save("shared.fit", b"\x00shared", minimal_report())
    store.link_member(record["id"], a["id"])
    store.link_member(record["id"], b["id"])
    assert len(store.members_for(record["id"])) == 2

    assert store.delete(record["id"]) is True

    assert store.members_for(record["id"]) == []
    assert store.count_for(a["id"]) == 0
    assert store.count_for(b["id"]) == 0


def test_count_for_ignores_a_link_whose_activity_is_gone(store, saved_activity):
    """count_for must join activities, or a stale link row inflates the header.

    The sqlite3 CLI leaves PRAGMA foreign_keys OFF, so clearing rows by hand is
    the natural way a link outlives its activity. Deriving the count from the
    join rows alone then makes the header disagree with the list beneath it -
    which is the bug the scoped count existed to prevent.
    """
    user = store.create_member("a@funston.com", "A", "pw")
    store.link_member(saved_activity, user["id"])
    assert store.count_for(user["id"]) == 1

    raw = sqlite3.connect(store.db_path)  # foreign_keys defaults to OFF
    raw.execute("DELETE FROM activities WHERE id = ?", (saved_activity,))
    raw.commit()
    raw.close()

    assert store.list_for(user["id"]) == []
    assert store.count_for(user["id"]) == 0


def test_count_admins(store):
    store.create_member("a@funston.com", "A", "pw", is_admin=True)
    store.create_member("b@funston.com", "B", "pw", is_admin=True)
    c = store.create_member("c@funston.com", "C", "pw")
    assert store.count_admins() == 2
    assert store.count_admins(active_only=True) == 2
    store.update_member(c["id"], is_active=False)
    assert store.count_admins(active_only=True) == 2
    store.create_member("d@funston.com", "D", "pw", is_admin=True, is_active=False)
    assert store.count_admins() == 3
    assert store.count_admins(active_only=True) == 2


def test_first_admin(store):
    assert store.first_admin() is None
    first = store.create_member("a@funston.com", "A", "pw", is_admin=True)
    store.create_member("b@funston.com", "B", "pw", is_admin=True)
    assert store.first_admin()["id"] == first["id"]


def test_first_admin_skips_deactivated(store):
    store.create_member("a@funston.com", "A", "pw", is_admin=True, is_active=False)
    live = store.create_member("b@funston.com", "B", "pw", is_admin=True)
    assert store.first_admin()["id"] == live["id"]


# -- the backfill must be *wired in*, not merely available -------------------


def _config_for(data_dir):
    class TmpConfig(Config):
        DATA_DIR = data_dir
        TESTING = True

    return TmpConfig


def test_startup_links_orphaned_activities(tmp_path):
    """create_app adopts orphaned activities - the store method alone proves nothing.

    Config.DATA_DIR is read at import time, so a config subclass is used rather
    than an environment variable.
    """
    data = tmp_path / "data"
    store = Store(data)
    record, _ = store.save("old.fit", b"\x00old", minimal_report())
    store.create_member("rpf@funston.com", "Richard", "pw", is_admin=True)

    create_app(_config_for(data))

    richard = Store(data).get_member_by_email("rpf@funston.com")
    assert Store(data).get_for(record["id"], richard["id"]) is not None


def test_startup_backfill_does_nothing_without_an_admin(tmp_path):
    """With no account yet, orphaned activities are left alone rather than guessed at."""
    data = tmp_path / "data"
    store = Store(data)
    store.save("old.fit", b"\x00old", minimal_report())

    create_app(_config_for(data))

    assert Store(data).list_members() == []


def test_startup_backfill_is_idempotent(tmp_path):
    data = tmp_path / "data"
    store = Store(data)
    store.save("old.fit", b"\x00old", minimal_report())
    store.create_member("rpf@funston.com", "Richard", "pw", is_admin=True)

    create_app(_config_for(data))
    create_app(_config_for(data))

    store = Store(data)
    richard = store.get_member_by_email("rpf@funston.com")
    assert store.count_for(richard["id"]) == 1


# -- the last-admin guard, atomically ---------------------------------------


def test_update_member_safely_refuses_the_last_admin_demoting_themselves(store):
    me = store.create_member("a@funston.com", "A", "pw", is_admin=True)
    with pytest.raises(LastAdminError):
        store.update_member_safely(me["id"], is_admin=False)
    assert store.get_member(me["id"])["is_admin"] == 1


def test_update_member_safely_refuses_the_last_admin_deactivating_themselves(store):
    me = store.create_member("a@funston.com", "A", "pw", is_admin=True)
    with pytest.raises(LastAdminError):
        store.update_member_safely(me["id"], is_active=False)
    assert store.count_admins(active_only=True) == 1


def test_update_member_safely_allows_a_spare_admin_to_be_demoted(store):
    store.create_member("a@funston.com", "A", "pw", is_admin=True)
    other = store.create_member("b@funston.com", "B", "pw", is_admin=True)
    updated = store.update_member_safely(other["id"], is_admin=False)
    assert updated["is_admin"] == 0
    assert store.count_admins(active_only=True) == 1


def test_update_member_safely_allows_reactivating(store):
    """Turning access back on never reduces the admin count."""
    me = store.create_member("a@funston.com", "A", "pw", is_admin=True, is_active=False)
    store.create_member("b@funston.com", "B", "pw", is_admin=True)
    updated = store.update_member_safely(me["id"], is_active=True)
    assert updated["is_active"] == 1


def test_update_member_safely_still_renames(store):
    store.create_member("a@funston.com", "A", "pw", is_admin=True)
    updated = store.update_member_safely(
        store.get_member_by_email("a@funston.com")["id"], name="Ann"
    )
    assert updated["name"] == "Ann"


def test_update_member_safely_returns_none_for_an_unknown_id(store):
    assert store.update_member_safely("nosuchid", name="X") is None


def test_update_member_safely_ignores_an_inactive_admin(store):
    """An already-dead admin is not one of the admins being protected."""
    store.create_member("a@funston.com", "A", "pw", is_admin=True)
    dead = store.create_member("d@funston.com", "D", "pw", is_admin=True, is_active=False)
    updated = store.update_member_safely(dead["id"], is_admin=False)
    assert updated["is_admin"] == 0


def test_two_racing_demotions_cannot_leave_zero_admins(store):
    """The guard is evaluated in the same transaction as the write.

    Counting first and updating after is check-then-act: two workers can both
    read a count of two, both decide the demotion is safe, and both apply it -
    reaching zero active admins with no way back into the admin screen. This is
    deterministic regardless of interleaving, because the second demotion must
    always be refused.
    """
    a = store.create_member("a@funston.com", "A", "pw", is_admin=True)
    b = store.create_member("b@funston.com", "B", "pw", is_admin=True)

    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def demote(user):
        barrier.wait()
        try:
            store.update_member_safely(user["id"], is_admin=False)
            outcomes.append("ok")
        except LastAdminError:
            outcomes.append("refused")

    threads = [threading.Thread(target=demote, args=(u,)) for u in (a, b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.count_admins(active_only=True) >= 1
    assert sorted(outcomes) == ["ok", "refused"]


# -- the member rename, on a database written before it ---------------------

OLD_SCHEMA = """
CREATE TABLE activities (
    id TEXT PRIMARY KEY, filename TEXT, sha256 TEXT, uploaded_at TEXT,
    start_time TEXT, sport TEXT, sub_sport TEXT, device TEXT, duration_s REAL,
    moving_s REAL, distance_m REAL, avg_speed_ms REAL, max_speed_ms REAL,
    avg_hr REAL, max_hr REAL, avg_cadence REAL, max_cadence REAL,
    ascent_m REAL, descent_m REAL, calories REAL, sample_count INTEGER,
    summary_json TEXT
);
CREATE TABLE users (
    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT,
    password_hash TEXT, is_admin INTEGER, is_active INTEGER, session_epoch INTEGER,
    created_at TEXT, modified_at TEXT
);
CREATE TABLE activity_paddlers (
    activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    PRIMARY KEY (activity_id, user_id)
);
CREATE INDEX activity_paddlers_user ON activity_paddlers (user_id);
INSERT INTO activities (id, filename, sha256, uploaded_at, start_time, summary_json)
    VALUES ('abc', 'x.fit', 'deadbeef', '2024-01-01', '2024-01-01', '{}');
INSERT INTO users (id, email, name, password_hash, is_admin, is_active,
                   session_epoch, created_at, modified_at)
    VALUES ('u1', 'a@b.com', 'A', 'x', 1, 1, 1, '2024-01-01', '2024-01-01');
INSERT INTO activity_paddlers (activity_id, user_id) VALUES ('abc', 'u1');
"""


def test_a_database_written_before_the_rename_keeps_its_sessions(tmp_path):
    """The join table was activity_paddlers. An existing database must migrate
    rather than come up with an empty table beside the old one."""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(data / "sport.db")
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()

    store = Store(data)

    with store._connect() as c:
        tables = {
            r[0]
            for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "activity_members" in tables
    assert "activity_paddlers" not in tables
    # The session and its link survived the move.
    assert store.count_for("u1") == 1
    assert store.get_for("abc", "u1") is not None
    assert store.members_for("abc")[0]["id"] == "u1"


def test_the_rename_migration_runs_once_and_is_harmless_afterwards(tmp_path):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(data / "sport.db")
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()

    Store(data)
    store = Store(data)

    assert store.count_for("u1") == 1


# -- changing an email keeps the account and everything hanging off it -------
#
# The links table is keyed on user_id, so an email change is an UPDATE on the
# existing row and the id never moves. These pin that down, because the failure
# mode would be silent: a re-created row would leave every link pointing at a
# member that no longer exists, and the sessions would simply vanish from view.


def test_change_email_updates_in_place_and_keeps_the_id(store):
    me = store.create_member("old@funston.com", "A", "pw")
    updated = store.change_email(me["id"], "new@funston.com")

    assert updated["id"] == me["id"]
    assert updated["email"] == "new@funston.com"


def test_change_email_keeps_every_session_link(store, saved_activity):
    me = store.create_member("old@funston.com", "A", "pw")
    store.link_member(saved_activity, me["id"])
    assert store.count_for(me["id"]) == 1

    store.change_email(me["id"], "new@funston.com")

    assert store.count_for(me["id"]) == 1
    assert store.get_for(saved_activity, me["id"]) is not None
    assert [m["email"] for m in store.members_for(saved_activity)] == ["new@funston.com"]


def test_change_email_does_not_touch_the_session_epoch(store):
    """The epoch is what a password change bumps to evict sessions.

    An email change must not bump it: the member is told the system stays, and
    being signed out of your own device for editing your address is not that.
    """
    me = store.create_member("old@funston.com", "A", "pw")
    before = store.session_epoch_for(me["id"])

    store.change_email(me["id"], "new@funston.com")

    assert store.session_epoch_for(me["id"]) == before


def test_change_email_keeps_the_password_admin_flag_and_activity(store):
    me = store.create_member("old@funston.com", "A", "pw", is_admin=True)

    updated = store.change_email(me["id"], "new@funston.com")

    assert updated["password_hash"] == me["password_hash"]
    assert updated["is_admin"] == 1
    assert updated["is_active"] == 1
    assert store.verify_member("new@funston.com", "pw")["id"] == me["id"]
    assert store.verify_member("old@funston.com", "pw") is None


def test_change_email_normalises_what_it_is_given(store):
    me = store.create_member("old@funston.com", "A", "pw")
    assert store.change_email(me["id"], "  New@Funston.COM  ")["email"] == "new@funston.com"


@pytest.mark.parametrize("taken", ["taken@funston.com", "TAKEN@funston.com", " Taken@Funston.com "])
def test_change_email_refuses_an_address_already_in_use(store, taken):
    """Case-insensitively, because the column is UNIQUE COLLATE NOCASE."""
    store.create_member("taken@funston.com", "T", "pw")
    me = store.create_member("mine@funston.com", "M", "pw")

    with pytest.raises(DuplicateEmail):
        store.change_email(me["id"], taken)

    # And the refusal left the member exactly as they were.
    assert store.get_member(me["id"])["email"] == "mine@funston.com"


def test_change_email_for_an_unknown_member_returns_none(store):
    assert store.change_email("nope", "x@funston.com") is None
