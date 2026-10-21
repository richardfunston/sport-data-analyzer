"""The admin screen: listing paddlers, creating them, changing passwords.

The lockout guards are the important part. With one admin, a self-demotion or a
self-deactivation would leave nobody able to reach the screen at all, and there
is no way back in through the UI.
"""

from __future__ import annotations

from conftest import minimal_report, sign_in


# -- who may reach it --------------------------------------------------------


def test_an_anonymous_visitor_is_redirected_not_forbidden(client):
    """403 would tell a stranger the route exists."""
    response = client.get("/admin/paddlers")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_an_anonymous_post_is_redirected_too(client):
    response = client.post("/admin/paddlers/new", data={"email": "x@y.z"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_a_signed_in_non_admin_gets_403(app, store):
    user = store.create_paddler("a@funston.com", "Anna", "pw", is_admin=False)
    response = sign_in(app.test_client(), user).get("/admin/paddlers")
    assert response.status_code == 403


def test_a_non_admin_cannot_create_a_paddler(app, store):
    user = store.create_paddler("a@funston.com", "Anna", "pw")
    response = sign_in(app.test_client(), user).post(
        "/admin/paddlers/new",
        data={"name": "Sneaky", "email": "sneaky@funston.com", "password": "pw"},
    )
    assert response.status_code == 403
    assert store.get_paddler_by_email("sneaky@funston.com") is None


# -- listing -----------------------------------------------------------------


def test_the_admin_sees_every_paddler(admin_client, store):
    store.create_paddler("anna@funston.com", "Anna", "pw")
    body = admin_client.get("/admin/paddlers").data.decode()
    assert "admin@funston.com" in body
    assert "anna@funston.com" in body
    assert "Anna" in body


def test_the_admin_screen_links_change_password(admin_client, store):
    user = store.create_paddler("anna@funston.com", "Anna", "pw")
    body = admin_client.get("/admin/paddlers").data.decode()
    assert f"/admin/paddlers/{user['id']}/password" in body


# -- creating ----------------------------------------------------------------


def test_the_admin_creates_a_paddler(admin_client, store):
    response = admin_client.post(
        "/admin/paddlers/new",
        data={"name": "Ben", "email": "ben@funston.com", "password": "a-good-one"},
    )
    assert response.status_code == 302

    created = store.get_paddler_by_email("ben@funston.com")
    assert created is not None
    assert created["name"] == "Ben"
    assert created["is_admin"] == 0
    assert store.verify_paddler("ben@funston.com", "a-good-one") is not None


def test_a_new_paddler_is_not_an_admin_by_default(admin_client, store):
    admin_client.post(
        "/admin/paddlers/new",
        data={"name": "Ben", "email": "ben@funston.com", "password": "pw"},
    )
    assert store.count_admins(active_only=True) == 1


def test_a_duplicate_email_is_reported_and_nothing_changes(admin_client, store):
    store.create_paddler("anna@funston.com", "Anna", "pw")
    response = admin_client.post(
        "/admin/paddlers/new",
        data={"name": "Someone Else", "email": "ANNA@funston.com", "password": "pw"},
    )
    assert response.status_code == 200
    assert b"already" in response.data.lower()
    assert store.get_paddler_by_email("anna@funston.com")["name"] == "Anna"
    assert len(store.list_paddlers()) == 2


def test_an_unusable_email_is_rejected(admin_client, store):
    response = admin_client.post(
        "/admin/paddlers/new",
        data={"name": "Ben", "email": "not-an-address", "password": "pw"},
    )
    assert response.status_code == 200
    assert b"email" in response.data.lower()
    assert store.get_paddler_by_email("not-an-address") is None


def test_a_missing_password_is_rejected(admin_client, store):
    response = admin_client.post(
        "/admin/paddlers/new",
        data={"name": "Ben", "email": "ben@funston.com", "password": ""},
    )
    assert response.status_code == 200
    assert b"password" in response.data.lower()
    assert store.get_paddler_by_email("ben@funston.com") is None


def test_a_missing_name_is_rejected(admin_client, store):
    response = admin_client.post(
        "/admin/paddlers/new",
        data={"name": "  ", "email": "ben@funston.com", "password": "pw"},
    )
    assert response.status_code == 200
    assert store.get_paddler_by_email("ben@funston.com") is None


# -- changing a password -----------------------------------------------------


def test_the_admin_changes_a_password(admin_client, store):
    user = store.create_paddler("anna@funston.com", "Anna", "the-old-one")
    response = admin_client.post(
        f"/admin/paddlers/{user['id']}/password", data={"password": "the-new-one"}
    )
    assert response.status_code == 302
    assert store.verify_paddler("anna@funston.com", "the-new-one") is not None
    assert store.verify_paddler("anna@funston.com", "the-old-one") is None


def test_changing_a_password_signs_that_paddler_out_everywhere(
    app, store, admin_client
):
    """The epoch bump: a stolen cookie must not outlive the password."""
    user = store.create_paddler("anna@funston.com", "Anna", "the-old-one")
    victim = sign_in(app.test_client(), user)
    assert victim.get("/").status_code == 200

    admin_client.post(
        f"/admin/paddlers/{user['id']}/password", data={"password": "the-new-one"}
    )

    assert victim.get("/").status_code == 302


def test_an_empty_new_password_is_refused(admin_client, store):
    user = store.create_paddler("anna@funston.com", "Anna", "the-old-one")
    response = admin_client.post(
        f"/admin/paddlers/{user['id']}/password", data={"password": ""}
    )
    assert response.status_code == 200
    assert store.verify_paddler("anna@funston.com", "the-old-one") is not None


def test_a_password_for_an_unknown_paddler_is_404(admin_client):
    response = admin_client.post(
        "/admin/paddlers/nosuchid/password", data={"password": "x"}
    )
    assert response.status_code == 404


# -- renaming, promoting, deactivating ---------------------------------------


def test_the_admin_renames_a_paddler(admin_client, store):
    user = store.create_paddler("anna@funston.com", "Anna", "pw")
    admin_client.post(
        f"/admin/paddlers/{user['id']}/update", data={"name": "Anna P"}
    )
    assert store.get_paddler(user["id"])["name"] == "Anna P"


def test_the_admin_promotes_another_paddler(admin_client, store):
    user = store.create_paddler("anna@funston.com", "Anna", "pw")
    admin_client.post(
        f"/admin/paddlers/{user['id']}/update", data={"is_admin": "1"}
    )
    assert store.get_paddler(user["id"])["is_admin"] == 1
    assert store.count_admins(active_only=True) == 2


def test_the_admin_deactivates_a_second_paddler(admin_client, store):
    user = store.create_paddler("anna@funston.com", "Anna", "pw")
    response = admin_client.post(
        f"/admin/paddlers/{user['id']}/update", data={"is_active": "0"}
    )
    assert response.status_code == 302
    assert store.get_paddler(user["id"])["is_active"] == 0


def test_an_update_for_an_unknown_paddler_is_404(admin_client):
    assert admin_client.post("/admin/paddlers/nosuchid/update", data={}).status_code == 404


def test_deactivating_a_sole_owner_does_not_lose_their_sessions(
    admin_client, store, app
):
    """Deactivation is the one admin action that can orphan a session.

    Removing the last paddler is refused outright, precisely because an unowned
    session is invisible to everyone. Deactivating its only paddler reaches the
    same state by another route - and the link row survives, so the startup
    backfill (which looks for no link at all) would not adopt it either. The
    sessions must be re-homed rather than quietly disappearing.
    """
    owner = store.create_paddler("owner@funston.com", "Owner", "pw")
    record, _ = store.save("theirs.fit", b"\x00theirs", minimal_report())
    store.link_paddler(record["id"], owner["id"])
    admin = admin_client.paddler

    # Before: the admin cannot see it.
    assert store.get_for(record["id"], admin["id"]) is None

    response = admin_client.post(
        f"/admin/paddlers/{owner['id']}/update", data={"is_active": "0"}
    )
    assert response.status_code == 302

    assert store.get_paddler(owner["id"])["is_active"] == 0
    # After: the session is still reachable by somebody.
    assert store.get_for(record["id"], admin["id"]) is not None
    assert record["id"] in [r["id"] for r in store.list_for(admin["id"])]
    assert "reassign" in response.headers["Location"].lower()


def test_reactivating_a_paddler_keeps_them_on_their_sessions(
    app, store, admin_client
):
    """Re-homing shares the session; it does not take it off the original owner."""
    owner = store.create_paddler("owner@funston.com", "Owner", "pw")
    record, _ = store.save("theirs.fit", b"\x00theirs", minimal_report())
    store.link_paddler(record["id"], owner["id"])

    admin_client.post(f"/admin/paddlers/{owner['id']}/update", data={"is_active": "0"})
    admin_client.post(f"/admin/paddlers/{owner['id']}/update", data={"is_active": "1"})

    assert store.get_for(record["id"], owner["id"]) is not None
    assert len(store.paddlers_for(record["id"])) == 2


def test_deactivating_someone_whose_sessions_are_shared_is_quiet(
    admin_client, store, app
):
    """No re-homing is needed when another active paddler is already on it."""
    owner = store.create_paddler("owner@funston.com", "Owner", "pw")
    mate = store.create_paddler("mate@funston.com", "Mate", "pw")
    record, _ = store.save("shared.fit", b"\x00shared", minimal_report())
    store.link_paddler(record["id"], owner["id"])
    store.link_paddler(record["id"], mate["id"])
    admin = admin_client.paddler

    response = admin_client.post(
        f"/admin/paddlers/{owner['id']}/update", data={"is_active": "0"}
    )

    assert response.status_code == 302
    assert store.get_for(record["id"], admin["id"]) is None
    assert "reassign" not in response.headers["Location"].lower()
    assert store.get_for(record["id"], mate["id"]) is not None


# -- the lockout guards ------------------------------------------------------


def test_the_last_admin_cannot_demote_themselves(admin_client, store):
    me = admin_client.paddler
    response = admin_client.post(
        f"/admin/paddlers/{me['id']}/update", data={"is_admin": "0"}
    )
    assert response.status_code == 400
    assert store.get_paddler(me["id"])["is_admin"] == 1
    assert store.count_admins(active_only=True) == 1


def test_the_last_admin_cannot_deactivate_themselves(admin_client, store):
    me = admin_client.paddler
    response = admin_client.post(
        f"/admin/paddlers/{me['id']}/update", data={"is_active": "0"}
    )
    assert response.status_code == 400
    assert store.count_admins(active_only=True) == 1


def test_a_second_admin_can_be_demoted(admin_client, store):
    other = store.create_paddler("ben@funston.com", "Ben", "pw", is_admin=True)
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/update", data={"is_admin": "0"}
    )
    assert response.status_code == 302
    assert store.get_paddler(other["id"])["is_admin"] == 0
    assert store.count_admins(active_only=True) == 1


def test_a_second_admin_can_be_deactivated(admin_client, store):
    other = store.create_paddler("ben@funston.com", "Ben", "pw", is_admin=True)
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/update", data={"is_active": "0"}
    )
    assert response.status_code == 302
    assert store.count_admins(active_only=True) == 1


def test_an_inactive_admin_does_not_count_towards_the_guard(admin_client, store):
    """One live admin plus one dead one is still one live admin."""
    store.create_paddler("ben@funston.com", "Ben", "pw", is_admin=True, is_active=False)
    assert store.count_admins(active_only=True) == 1

    me = admin_client.paddler
    response = admin_client.post(
        f"/admin/paddlers/{me['id']}/update", data={"is_admin": "0"}
    )
    assert response.status_code == 400
