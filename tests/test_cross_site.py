"""Cross-site write protection.

A state-changing request whose ``Origin`` (or ``Referer``) names another site is
refused. ``SameSite=Lax`` already blocks the ordinary cross-site form post, so
this closes what Lax does not: same-site hosts under a shared parent domain, and
clients that ignore SameSite. A header comparison, not a dependency.
"""

from __future__ import annotations

import io

SAME = "http://localhost"
CROSS = "https://evil.example"


# -- the admin screen --------------------------------------------------------


def test_a_write_from_another_site_is_refused(admin_client, store):
    other = store.create_paddler("o@funston.com", "O", "pw")
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/update",
        data={"is_admin": "1"},
        headers={"Origin": CROSS},
    )
    assert response.status_code == 403
    assert store.get_paddler(other["id"])["is_admin"] == 0


def test_a_write_from_this_site_is_allowed(admin_client, store):
    other = store.create_paddler("o@funston.com", "O", "pw")
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/update",
        data={"is_admin": "1"},
        headers={"Origin": SAME},
    )
    assert response.status_code == 302
    assert store.get_paddler(other["id"])["is_admin"] == 1


def test_a_write_with_no_origin_at_all_is_allowed(admin_client, store):
    """curl, scripts and health probes send neither header, and a browser cannot
    suppress Origin on a cross-site POST - it is a forbidden header."""
    other = store.create_paddler("o@funston.com", "O", "pw")
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/update", data={"is_admin": "1"}
    )
    assert response.status_code == 302


def test_a_cross_site_referer_is_used_when_origin_is_absent(admin_client, store):
    other = store.create_paddler("o@funston.com", "O", "pw")
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/update",
        data={"is_admin": "1"},
        headers={"Referer": f"{CROSS}/somewhere"},
    )
    assert response.status_code == 403


def test_a_matching_referer_is_allowed(admin_client, store):
    other = store.create_paddler("o@funston.com", "O", "pw")
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/update",
        data={"is_admin": "1"},
        headers={"Referer": f"{SAME}/admin/paddlers"},
    )
    assert response.status_code == 302


def test_a_null_origin_is_refused(admin_client, store):
    """A sandboxed iframe sends ``Origin: null``, which is not this site."""
    other = store.create_paddler("o@funston.com", "O", "pw")
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/update",
        data={"is_admin": "1"},
        headers={"Origin": "null"},
    )
    assert response.status_code == 403


def test_a_safe_method_is_not_checked(admin_client):
    """GET must keep working, or a link from anywhere would break."""
    assert (
        admin_client.get("/admin/paddlers", headers={"Origin": CROSS}).status_code
        == 200
    )


def test_it_covers_the_admin_password_change(admin_client, store):
    other = store.create_paddler("o@funston.com", "O", "the-old-one")
    response = admin_client.post(
        f"/admin/paddlers/{other['id']}/password",
        data={"password": "the-new-one"},
        headers={"Origin": CROSS},
    )
    assert response.status_code == 403
    assert store.verify_paddler("o@funston.com", "the-old-one") is not None


def test_it_covers_creating_a_paddler(admin_client, store):
    response = admin_client.post(
        "/admin/paddlers/new",
        data={"name": "Sneaky", "email": "sneaky@funston.com", "password": "pw"},
        headers={"Origin": CROSS},
    )
    assert response.status_code == 403
    assert store.get_paddler_by_email("sneaky@funston.com") is None


# -- and the rest of the app -------------------------------------------------


def test_it_covers_deleting_a_session(signed_in, store, real_activity):
    store.link_paddler(real_activity, signed_in.paddler["id"])
    response = signed_in.post(
        f"/api/a/{real_activity}/delete", headers={"Origin": CROSS}
    )
    assert response.status_code == 403
    assert store.get(real_activity) is not None


def test_it_covers_an_upload(signed_in, canoe_fit):
    response = signed_in.post(
        "/api/upload",
        data={"files": (io.BytesIO(canoe_fit), "s.fit")},
        content_type="multipart/form-data",
        headers={"Origin": CROSS},
    )
    assert response.status_code == 403


def test_it_covers_sharing_a_session(signed_in, store, saved_activity):
    store.link_paddler(saved_activity, signed_in.paddler["id"])
    other = store.create_paddler("o@funston.com", "O", "pw")
    response = signed_in.post(
        f"/api/a/{saved_activity}/paddlers",
        json={"add": other["id"]},
        headers={"Origin": CROSS},
    )
    assert response.status_code == 403
    assert store.get_for(saved_activity, other["id"]) is None


def test_it_covers_signing_in(client, store):
    """A cross-site login POST is the classic login-CSRF: it signs a victim into
    somebody else's account without them noticing."""
    store.create_paddler("rpf@funston.com", "Richard", "pw")
    response = client.post(
        "/login",
        data={"email": "rpf@funston.com", "password": "pw"},
        headers={"Origin": CROSS},
    )
    assert response.status_code == 403


def test_it_covers_signing_out(signed_in):
    assert signed_in.post("/logout", headers={"Origin": CROSS}).status_code == 403
