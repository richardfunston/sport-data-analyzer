"""Who can see which session, and what happens to everyone else.

The negative cases are the point of this feature: a paddler who is not linked to
a session must not be able to reach it, and must not be able to tell that it
exists. Those are 404s, not 403s.
"""

from __future__ import annotations

import io

from conftest import minimal_report, sign_in


def _upload(client, raw: bytes, filename: str = "session.fit"):
    return client.post(
        "/api/upload",
        data={"files": (io.BytesIO(raw), filename)},
        content_type="multipart/form-data",
    )


# -- signing in is required for everything but healthz -----------------------


def test_index_redirects_an_anonymous_visitor_to_the_sign_in_page(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_report_redirects_an_anonymous_visitor(client, saved_activity):
    response = client.get(f"/a/{saved_activity}")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_download_redirects_an_anonymous_visitor(client, saved_activity):
    assert client.get(f"/a/{saved_activity}/download").status_code == 302


def test_delete_redirects_an_anonymous_visitor(client, saved_activity):
    assert client.post(f"/api/a/{saved_activity}/delete").status_code == 302


def test_upload_redirects_an_anonymous_visitor(client, canoe_fit):
    assert _upload(client, canoe_fit).status_code == 302


def test_healthz_stays_public(client):
    assert client.get("/healthz").status_code == 200


def test_healthz_does_not_leak_the_global_count(client, saved_activity, another_activity):
    """A total readable by a stranger discloses the size of the deployment."""
    assert client.get("/healthz").get_json() == {"ok": True}


# -- the session is re-checked on every request ------------------------------


def test_deactivated_paddler_is_signed_out_on_the_next_request(signed_in, store):
    store.update_paddler(signed_in.paddler["id"], is_active=False)
    assert signed_in.get("/").status_code == 302


def test_password_change_evicts_an_existing_session(signed_in, store):
    """Otherwise a stolen cookie outlives the password it belonged to."""
    store.set_password(signed_in.paddler["id"], "a-new-one")
    assert signed_in.get("/").status_code == 302


def test_a_live_session_still_works(signed_in):
    assert signed_in.get("/").status_code == 200


# -- visibility --------------------------------------------------------------


def test_index_lists_only_linked_sessions(signed_in, store, saved_activity, another_activity):
    store.link_paddler(saved_activity, signed_in.paddler["id"])

    body = signed_in.get("/").data.decode()
    assert saved_activity in body
    assert another_activity not in body


def test_index_count_is_scoped_to_the_paddler(signed_in, store, saved_activity, another_activity):
    """Two sessions exist; only one is this paddler's, so the header says (1)."""
    store.link_paddler(saved_activity, signed_in.paddler["id"])

    body = signed_in.get("/").data.decode()
    assert "(1)" in body
    assert "(2)" not in body


def test_report_is_404_for_an_unlinked_paddler(signed_in, another_activity):
    """404 rather than 403: a 403 would confirm the id exists."""
    response = signed_in.get(f"/a/{another_activity}")
    assert response.status_code == 404


def test_download_is_404_for_an_unlinked_paddler(signed_in, another_activity):
    assert signed_in.get(f"/a/{another_activity}/download").status_code == 404


def test_delete_is_404_for_an_unlinked_paddler(signed_in, store, another_activity):
    assert signed_in.post(f"/api/a/{another_activity}/delete").status_code == 404
    assert store.get(another_activity) is not None


def test_a_linked_paddler_can_open_the_report(signed_in, store, real_activity):
    store.link_paddler(real_activity, signed_in.paddler["id"])
    assert signed_in.get(f"/a/{real_activity}").status_code == 200


def test_a_linked_paddler_can_delete(signed_in, store, saved_activity):
    store.link_paddler(saved_activity, signed_in.paddler["id"])
    assert signed_in.post(f"/api/a/{saved_activity}/delete").status_code == 200
    assert store.get(saved_activity) is None


# -- uploading ---------------------------------------------------------------


def test_upload_links_the_uploader(signed_in, store, canoe_fit):
    body = _upload(signed_in, canoe_fit).get_json()
    assert body["results"][0]["ok"] is True

    activity_id = body["results"][0]["id"]
    assert store.get_for(activity_id, signed_in.paddler["id"]) is not None
    assert signed_in.get(f"/a/{activity_id}").status_code == 200
    assert signed_in.get("/").data.decode().count(activity_id) >= 1


def test_reupload_of_an_identical_file_links_a_second_paddler(app, store, canoe_fit):
    """The dedupe path must link the new uploader, not just return the row.

    This is the case the feature exists for: the second paddler in a canoe
    adding the same file off their own watch. If the existing-duplicate return
    skips the link, the session never appears for them.
    """
    a = store.create_paddler("a@funston.com", "A", "pw")
    b = store.create_paddler("b@funston.com", "B", "pw")
    first = sign_in(app.test_client(), a)
    second = sign_in(app.test_client(), b)

    first_id = _upload(first, canoe_fit).get_json()["results"][0]["id"]
    second_result = _upload(second, canoe_fit).get_json()["results"][0]

    assert second_result["id"] == first_id
    assert second_result["created"] is False
    assert store.get_for(first_id, b["id"]) is not None
    assert second.get(f"/a/{first_id}").status_code == 200


def test_a_paddler_still_cannot_see_a_session_they_are_not_linked_to(
    app, store, saved_activity
):
    """The other half of the previous test: sharing is explicit, not automatic."""
    a = store.create_paddler("a@funston.com", "A", "pw")
    b = store.create_paddler("b@funston.com", "B", "pw")
    store.link_paddler(saved_activity, a["id"])

    assert sign_in(app.test_client(), b).get(f"/a/{saved_activity}").status_code == 404


# -- sharing a session -------------------------------------------------------


def _two_paddlers(store):
    a = store.create_paddler("a@funston.com", "Anna", "pw")
    b = store.create_paddler("b@funston.com", "Ben", "pw")
    return a, b


def test_a_linked_paddler_can_add_another(app, store, saved_activity):
    a, b = _two_paddlers(store)
    store.link_paddler(saved_activity, a["id"])

    response = sign_in(app.test_client(), a).post(
        f"/api/a/{saved_activity}/paddlers", json={"add": b["id"]}
    )

    assert response.status_code == 200
    assert store.get_for(saved_activity, b["id"]) is not None


def test_adding_the_same_paddler_twice_is_harmless(app, store, saved_activity):
    a, b = _two_paddlers(store)
    store.link_paddler(saved_activity, a["id"])
    client = sign_in(app.test_client(), a)

    client.post(f"/api/a/{saved_activity}/paddlers", json={"add": b["id"]})
    response = client.post(f"/api/a/{saved_activity}/paddlers", json={"add": b["id"]})

    assert response.status_code == 200
    assert len(store.paddlers_for(saved_activity)) == 2


def test_a_linked_paddler_can_remove_another(app, store, saved_activity):
    a, b = _two_paddlers(store)
    store.link_paddler(saved_activity, a["id"])
    store.link_paddler(saved_activity, b["id"])

    response = sign_in(app.test_client(), a).post(
        f"/api/a/{saved_activity}/paddlers", json={"remove": b["id"]}
    )

    assert response.status_code == 200
    assert store.get_for(saved_activity, b["id"]) is None
    assert store.get_for(saved_activity, a["id"]) is not None


def test_cannot_remove_the_last_paddler(app, store, saved_activity):
    """An unowned session is invisible to everyone - indistinguishable from lost."""
    a = store.create_paddler("a@funston.com", "Anna", "pw")
    store.link_paddler(saved_activity, a["id"])

    response = sign_in(app.test_client(), a).post(
        f"/api/a/{saved_activity}/paddlers", json={"remove": a["id"]}
    )

    assert response.status_code == 400
    assert store.get_for(saved_activity, a["id"]) is not None


def test_an_unlinked_paddler_cannot_share_someone_elses_session(
    app, store, saved_activity
):
    a, b = _two_paddlers(store)
    c = store.create_paddler("c@funston.com", "Cass", "pw")
    store.link_paddler(saved_activity, a["id"])

    response = sign_in(app.test_client(), c).post(
        f"/api/a/{saved_activity}/paddlers", json={"add": b["id"]}
    )

    assert response.status_code == 404
    assert store.get_for(saved_activity, b["id"]) is None


def test_sharing_requires_signing_in(client, saved_activity):
    assert client.post(f"/api/a/{saved_activity}/paddlers", json={}).status_code == 302


def test_cannot_add_an_unknown_paddler(app, store, saved_activity):
    a = store.create_paddler("a@funston.com", "Anna", "pw")
    store.link_paddler(saved_activity, a["id"])

    response = sign_in(app.test_client(), a).post(
        f"/api/a/{saved_activity}/paddlers", json={"add": "nosuchid"}
    )

    assert response.status_code == 400
    assert len(store.paddlers_for(saved_activity)) == 1


def test_a_malformed_request_is_a_bad_request(app, store, saved_activity):
    a = store.create_paddler("a@funston.com", "Anna", "pw")
    store.link_paddler(saved_activity, a["id"])

    response = sign_in(app.test_client(), a).post(
        f"/api/a/{saved_activity}/paddlers", json={"neither": "add-nor-remove"}
    )

    assert response.status_code == 400


def test_the_report_page_lists_the_paddlers_and_offers_the_others(
    app, store, real_activity
):
    a, b = _two_paddlers(store)
    store.link_paddler(real_activity, a["id"])

    body = sign_in(app.test_client(), a).get(f"/a/{real_activity}").data.decode()

    assert "Anna" in body
    assert "Ben" in body  # offered to add
    assert f"/api/a/{real_activity}/paddlers" in body

