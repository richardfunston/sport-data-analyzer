"""The paddler table and the per-session paddler links."""

from __future__ import annotations

import pytest

from app import create_app
from app.config import Config
from app.storage import DuplicateEmail, Store

from conftest import minimal_report


def test_create_and_fetch_paddler(store):
    user = store.create_paddler(
        "rpf@funston.com", "Richard Funston", "8983498131", is_admin=True
    )
    assert user["email"] == "rpf@funston.com"
    assert user["name"] == "Richard Funston"
    assert user["is_admin"] == 1
    assert user["password_hash"] != "8983498131"
    assert store.get_paddler_by_email("rpf@funston.com")["id"] == user["id"]
    assert store.get_paddler(user["id"])["name"] == "Richard Funston"


def test_created_paddler_is_active_and_not_admin_by_default(store):
    user = store.create_paddler("a@funston.com", "A", "pw")
    assert user["is_active"] == 1
    assert user["is_admin"] == 0


def test_email_is_lowercased_and_trimmed(store):
    user = store.create_paddler("  RPF@Funston.COM  ", "Richard", "pw")
    assert user["email"] == "rpf@funston.com"


def test_duplicate_email_rejected(store):
    store.create_paddler("rpf@funston.com", "Richard", "pw")
    with pytest.raises(DuplicateEmail):
        store.create_paddler("RPF@funston.com", "Someone Else", "pw")


def test_verify_paddler(store):
    store.create_paddler("rpf@funston.com", "Richard", "8983498131")
    assert store.verify_paddler("rpf@funston.com", "8983498131") is not None
    assert store.verify_paddler(" RPF@Funston.com ", "8983498131") is not None
    assert store.verify_paddler("rpf@funston.com", "wrong") is None
    assert store.verify_paddler("nobody@funston.com", "8983498131") is None


def test_verify_rejects_a_deactivated_paddler(store):
    user = store.create_paddler("rpf@funston.com", "Richard", "pw")
    store.update_paddler(user["id"], is_active=False)
    assert store.verify_paddler("rpf@funston.com", "pw") is None


def test_set_password_bumps_session_epoch(store):
    user = store.create_paddler("rpf@funston.com", "Richard", "old")
    before = store.session_epoch_for(user["id"])
    assert store.set_password(user["id"], "new") is True
    assert store.session_epoch_for(user["id"]) == before + 1
    assert store.verify_paddler("rpf@funston.com", "new") is not None
    assert store.verify_paddler("rpf@funston.com", "old") is None


def test_session_epoch_for_unknown_paddler_is_none(store):
    assert store.session_epoch_for("nope") is None


def test_set_password_for_unknown_paddler_is_false(store):
    assert store.set_password("nope", "pw") is False


def test_update_paddler_returns_none_for_unknown_id(store):
    assert store.update_paddler("nope", name="X") is None


def test_update_paddler_renames_and_promotes(store):
    user = store.create_paddler("a@funston.com", "A", "pw")
    updated = store.update_paddler(user["id"], name="Ann", is_admin=True)
    assert updated["name"] == "Ann"
    assert updated["is_admin"] == 1
    assert store.get_paddler(user["id"])["name"] == "Ann"


def test_deactivating_does_not_delete_the_row(store):
    user = store.create_paddler("rpf@funston.com", "Richard", "pw")
    updated = store.update_paddler(user["id"], is_active=False)
    assert updated["is_active"] == 0
    assert store.get_paddler(user["id"]) is not None


def test_link_and_visibility(store, saved_activity):
    a = store.create_paddler("a@funston.com", "A", "pw")
    b = store.create_paddler("b@funston.com", "B", "pw")
    assert store.link_paddler(saved_activity, a["id"]) is True
    assert store.link_paddler(saved_activity, a["id"]) is False  # already linked
    assert [p["id"] for p in store.paddlers_for(saved_activity)] == [a["id"]]
    assert store.get_for(saved_activity, a["id"]) is not None
    assert store.get_for(saved_activity, b["id"]) is None
    assert [r["id"] for r in store.list_for(a["id"])] == [saved_activity]
    assert store.list_for(b["id"]) == []
    assert store.count_for(a["id"]) == 1
    assert store.count_for(b["id"]) == 0
    assert store.unlink_paddler(saved_activity, a["id"]) is True
    assert store.unlink_paddler(saved_activity, a["id"]) is False
    assert store.get_for(saved_activity, a["id"]) is None


def test_a_session_can_have_several_paddlers(store, saved_activity):
    """A two-paddler canoe is one session linked twice."""
    a = store.create_paddler("a@funston.com", "A", "pw")
    b = store.create_paddler("b@funston.com", "B", "pw")
    store.link_paddler(saved_activity, a["id"])
    store.link_paddler(saved_activity, b["id"])
    assert {p["id"] for p in store.paddlers_for(saved_activity)} == {a["id"], b["id"]}
    assert store.count_for(a["id"]) == 1
    assert store.count_for(b["id"]) == 1


def test_backfill_links_only_unlinked_activities(store):
    """An activity with no link at all is adopted; a linked one is left alone."""
    orphan, _ = store.save("orphan.fit", b"\x00orphan", minimal_report())
    mine, _ = store.save("mine.fit", b"\x00mine", minimal_report(start_time="2024-07-01T00:00:00+00:00"))
    richard = store.create_paddler("rpf@funston.com", "Richard", "pw", is_admin=True)
    other = store.create_paddler("o@funston.com", "O", "pw")
    store.link_paddler(mine["id"], other["id"])

    assert store.backfill_paddlers(richard["id"]) == 1
    assert store.get_for(orphan["id"], richard["id"]) is not None
    assert store.get_for(mine["id"], richard["id"]) is None
    assert store.get_for(mine["id"], other["id"]) is not None
    # A second run finds nothing left to adopt.
    assert store.backfill_paddlers(richard["id"]) == 0
    assert store.backfill_paddlers(other["id"]) == 0


def test_count_admins(store):
    store.create_paddler("a@funston.com", "A", "pw", is_admin=True)
    store.create_paddler("b@funston.com", "B", "pw", is_admin=True)
    c = store.create_paddler("c@funston.com", "C", "pw")
    assert store.count_admins() == 2
    assert store.count_admins(active_only=True) == 2
    store.update_paddler(c["id"], is_active=False)
    assert store.count_admins(active_only=True) == 2
    store.create_paddler("d@funston.com", "D", "pw", is_admin=True, is_active=False)
    assert store.count_admins() == 3
    assert store.count_admins(active_only=True) == 2


def test_first_admin(store):
    assert store.first_admin() is None
    first = store.create_paddler("a@funston.com", "A", "pw", is_admin=True)
    store.create_paddler("b@funston.com", "B", "pw", is_admin=True)
    assert store.first_admin()["id"] == first["id"]


def test_first_admin_skips_deactivated(store):
    store.create_paddler("a@funston.com", "A", "pw", is_admin=True, is_active=False)
    live = store.create_paddler("b@funston.com", "B", "pw", is_admin=True)
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
    store.create_paddler("rpf@funston.com", "Richard", "pw", is_admin=True)

    create_app(_config_for(data))

    richard = Store(data).get_paddler_by_email("rpf@funston.com")
    assert Store(data).get_for(record["id"], richard["id"]) is not None


def test_startup_backfill_does_nothing_without_an_admin(tmp_path):
    """With no account yet, orphaned activities are left alone rather than guessed at."""
    data = tmp_path / "data"
    store = Store(data)
    store.save("old.fit", b"\x00old", minimal_report())

    create_app(_config_for(data))

    assert Store(data).list_paddlers() == []


def test_startup_backfill_is_idempotent(tmp_path):
    data = tmp_path / "data"
    store = Store(data)
    store.save("old.fit", b"\x00old", minimal_report())
    store.create_paddler("rpf@funston.com", "Richard", "pw", is_admin=True)

    create_app(_config_for(data))
    create_app(_config_for(data))

    store = Store(data)
    richard = store.get_paddler_by_email("rpf@funston.com")
    assert store.count_for(richard["id"]) == 1
