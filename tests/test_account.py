"""Changing a member's email.

Email is the sign-in identity, so this is the one thing a member edits about their
own account. The account itself has to survive it: the session links are keyed on
the member's ``id``, never the email, so the change is an UPDATE on the existing
row. The id does not move, which is why every link, the password, the admin flag
and the live session all come through untouched - by construction, not by
re-linking afterwards.
"""

from __future__ import annotations

import io

import pytest

from conftest import sign_in

CROSS = "https://evil.example"


def upload(client, payload: bytes, filename: str = "canoe-session.fit"):
    return client.post(
        "/api/upload",
        data={"files": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )


@pytest.fixture()
def me(store):
    return store.create_member("old@funston.com", "Old Name", "pw")


@pytest.fixture()
def member_client(app, store, me):
    """A client signed in as the member whose email is about to change."""
    return sign_in(app.test_client(), me)


# -- reaching the page ------------------------------------------------------


def test_the_account_page_shows_the_current_email(member_client):
    response = member_client.get("/account")
    assert response.status_code == 200
    assert b"old@funston.com" in response.data


def test_the_account_page_needs_a_session(app):
    response = app.test_client().get("/account")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_the_nav_links_to_the_account_page(member_client):
    body = member_client.get("/").get_data(as_text=True)
    assert 'href="/account"' in body


# -- the change -------------------------------------------------------------


def test_a_member_can_change_their_own_email(member_client, store, me):
    response = member_client.post("/account", data={"email": "new@funston.com"})

    assert response.status_code == 302
    assert store.get_member(me["id"])["email"] == "new@funston.com"


def test_the_member_id_is_unchanged(member_client, store, me):
    """Everything else is keyed on this, so it must not move."""
    member_client.post("/account", data={"email": "new@funston.com"})
    assert store.get_member(me["id"])["id"] == me["id"]


def test_the_member_stays_signed_in(member_client, store, me):
    """The requirement: the system stays. No re-login, no eviction."""
    member_client.post("/account", data={"email": "new@funston.com"})

    assert member_client.get("/").status_code == 200
    assert store.session_epoch_for(me["id"]) == me["session_epoch"]


def test_the_session_links_survive(member_client, store, me, canoe_fit):
    """The other half of the requirement: links off sessions stay."""
    upload(member_client, canoe_fit)
    assert store.count_for(me["id"]) == 1

    member_client.post("/account", data={"email": "new@funston.com"})

    assert store.count_for(me["id"]) == 1
    # Still visible in the list, still legible on the page.
    assert "Canoeing" in member_client.get("/").get_data(as_text=True)


def test_the_password_and_admin_flag_survive(member_client, store, me):
    member_client.post("/account", data={"email": "new@funston.com"})

    updated = store.get_member(me["id"])
    assert updated["password_hash"] == me["password_hash"]
    assert updated["is_admin"] == me["is_admin"]
    assert updated["is_active"] == 1


def test_the_new_email_signs_in_and_the_old_one_does_not(member_client, store, me):
    member_client.post("/account", data={"email": "new@funston.com"})

    assert store.verify_member("new@funston.com", "pw")["id"] == me["id"]
    assert store.verify_member("old@funston.com", "pw") is None


def test_the_email_is_trimmed_and_case_folded(member_client, store, me):
    member_client.post("/account", data={"email": "  New@Funston.COM  "})
    assert store.get_member(me["id"])["email"] == "new@funston.com"


def test_the_change_is_confirmed_on_the_page(member_client, store, me):
    response = member_client.post(
        "/account", data={"email": "new@funston.com"}, follow_redirects=True
    )
    assert response.status_code == 200
    assert b"new@funston.com" in response.data


# -- refusals ---------------------------------------------------------------


def test_an_email_already_in_use_is_refused(member_client, store, me):
    store.create_member("taken@funston.com", "T", "pw")

    response = member_client.post("/account", data={"email": "taken@funston.com"})

    assert response.status_code == 400
    assert b"already" in response.data.lower()
    assert store.get_member(me["id"])["email"] == "old@funston.com"


def test_a_duplicate_is_refused_regardless_of_case(member_client, store, me):
    """The column is COLLATE NOCASE, so this must collide, not create a twin."""
    store.create_member("taken@funston.com", "T", "pw")

    response = member_client.post("/account", data={"email": "TAKEN@Funston.com"})

    assert response.status_code == 400
    assert len(store.list_members()) == 2


def test_something_that_is_not_an_email_is_refused(member_client, store, me):
    for candidate in ("", "   ", "nope", "no@", "@no.com"):
        response = member_client.post("/account", data={"email": candidate})
        assert response.status_code == 400, candidate
        assert store.get_member(me["id"])["email"] == "old@funston.com"


def test_a_cross_site_change_is_refused(member_client, store, me):
    response = member_client.post(
        "/account", data={"email": "evil@example.com"}, headers={"Origin": CROSS}
    )

    assert response.status_code == 403
    assert store.get_member(me["id"])["email"] == "old@funston.com"


# -- admins can fix anyone's -------------------------------------------------


def test_an_admin_can_change_another_members_email(admin_client, store):
    other = store.create_member("typo@funston.com", "T", "pw")

    response = admin_client.post(
        f"/admin/members/{other['id']}/update",
        data={"name": "T", "email": "fixed@funston.com"},
    )

    assert response.status_code == 302
    assert store.get_member(other["id"])["email"] == "fixed@funston.com"


def test_an_admin_change_keeps_that_members_links(admin_client, store, saved_activity):
    other = store.create_member("typo@funston.com", "T", "pw")
    store.link_member(saved_activity, other["id"])

    admin_client.post(
        f"/admin/members/{other['id']}/update",
        data={"name": "T", "email": "fixed@funston.com"},
    )

    assert store.count_for(other["id"]) == 1
    assert store.get_for(saved_activity, other["id"]) is not None


def test_an_admin_cannot_take_an_email_that_is_in_use(admin_client, store):
    store.create_member("taken@funston.com", "T", "pw")
    other = store.create_member("mine@funston.com", "M", "pw")

    response = admin_client.post(
        f"/admin/members/{other['id']}/update",
        data={"name": "M", "email": "taken@funston.com"},
    )

    assert response.status_code == 400
    assert store.get_member(other["id"])["email"] == "mine@funston.com"


def test_a_non_admin_cannot_change_someone_elses_email(member_client, store, me):
    other = store.create_member("other@funston.com", "O", "pw")

    response = member_client.post(
        f"/admin/members/{other['id']}/update",
        data={"name": "O", "email": "hijacked@funston.com"},
    )

    assert response.status_code == 403
    assert store.get_member(other["id"])["email"] == "other@funston.com"
