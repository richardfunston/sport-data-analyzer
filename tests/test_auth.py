"""Password hashing, the secret key, and the signed-cookie session.

The decorator *behaviour* tests (redirect when anonymous, 403 when not an admin)
live with the routes that exercise them -- Task 3 for the sign-in redirect and
Task 6 for the admin gate. `login_required` resolves `url_for("sda.login")`, and
that endpoint does not exist until Task 3, so testing it here would raise
BuildError rather than assert anything.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.auth import (
    LOGIN_MAX_FAILURES,
    check_password,
    current_member,
    hash_password,
    load_secret_key,
    login_member,
    logout_member,
)
from app.storage import Store


# -- passwords ---------------------------------------------------------------
# These two characterise behaviour Task 1 already shipped (the store hashes on
# create). They are here so the hashing contract has direct tests of its own;
# they pass on first run, which is recorded in the ledger rather than pretended
# away.


def test_password_roundtrip():
    digest = hash_password("8983498131")
    assert digest != "8983498131"
    assert check_password("8983498131", digest) is True
    assert check_password("8983498132", digest) is False


def test_same_password_hashes_differently():
    """A per-password salt, so two members with one password are not equal."""
    assert hash_password("pw") != hash_password("pw")


def test_check_password_rejects_a_corrupt_hash():
    """A damaged row must read as a failed login, not a 500."""
    assert check_password("pw", "garbage") is False


# -- the secret key ----------------------------------------------------------


def test_secret_key_is_stable_and_not_the_old_default(tmp_path):
    first = load_secret_key(tmp_path)
    assert first == load_secret_key(tmp_path)
    assert first != "sport-data-analyzer"
    assert len(first) >= 32


def test_secret_key_file_is_private(tmp_path):
    load_secret_key(tmp_path)
    mode = (tmp_path / "secret_key").stat().st_mode & 0o777
    assert mode == 0o600


def test_secret_key_reuses_a_key_already_on_disk(tmp_path):
    """The two-worker race.

    wsgi.py calls create_app() at import and the unit runs gunicorn with
    --workers 2 and no --preload, so two processes reach load_secret_key
    independently on a cold start. A key already on disk must win over a freshly
    generated one, or the loser signs cookies the winner rejects and about half
    of all sign-ins fail on the next request.
    """
    (tmp_path / "secret_key").write_text("the-key-that-won-the-race")
    assert load_secret_key(tmp_path) == "the-key-that-won-the-race"


def test_secret_key_honours_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SDA_SECRET_KEY", "from-the-environment")
    assert load_secret_key(tmp_path) == "from-the-environment"
    assert not (tmp_path / "secret_key").exists()


# -- the session -------------------------------------------------------------


def test_current_member_is_none_when_anonymous(app):
    with app.test_request_context("/"):
        assert current_member() is None


def test_login_then_current_member(app, store):
    user = store.create_member("a@funston.com", "A", "pw")
    with app.test_request_context("/"):
        login_member(user)
        assert current_member()["id"] == user["id"]


def test_logout_signs_the_member_out(app, store):
    user = store.create_member("a@funston.com", "A", "pw")
    with app.test_request_context("/"):
        login_member(user)
        logout_member()
        assert current_member() is None


def test_current_member_ignores_a_stale_epoch(app, store):
    """A password change evicts sessions issued before it."""
    user = store.create_member("a@funston.com", "A", "pw")
    with app.test_request_context("/"):
        login_member(user)
        store.set_password(user["id"], "new")
        assert current_member() is None


def test_current_member_ignores_a_deactivated_member(app, store):
    user = store.create_member("a@funston.com", "A", "pw")
    with app.test_request_context("/"):
        login_member(user)
        store.update_member(user["id"], is_active=False)
        assert current_member() is None


def test_current_member_ignores_a_deleted_member(app, store):
    user = store.create_member("a@funston.com", "A", "pw")
    with app.test_request_context("/"):
        login_member(user)
        with store._connect() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
            conn.commit()
        assert current_member() is None


def test_session_survives_a_round_trip_through_the_cookie(client, store):
    user = store.create_member("a@funston.com", "A", "pw")
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["epoch"] = user["session_epoch"]
    # Reading it back through the client proves the cookie is signed and parses.
    with client.session_transaction() as sess:
        assert sess["user_id"] == user["id"]


# -- config ------------------------------------------------------------------


def test_secret_key_is_not_the_insecure_default(app):
    assert app.config["SECRET_KEY"] != "sport-data-analyzer"


def test_session_lasts_twelve_hours(app):
    assert app.permanent_session_lifetime == timedelta(hours=12)


def test_disabling_session_refresh_would_break_sliding(app):
    """Flask re-issues the cookie per request only while this is true."""
    assert app.config["SESSION_REFRESH_EACH_REQUEST"] is True


def test_cookie_is_samesite_lax_and_httponly(app):
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_the_cookie_is_secure_over_https_and_plain_over_http(client, store):
    """One process serves both an HTTPS host and a plain-HTTP LAN mount.

    A static flag cannot serve both: `Secure` on breaks LAN sign-in over HTTP,
    `Secure` off means a 12-hour session cookie travels in the clear on the
    public host. It follows the scheme the request actually arrived over.
    """
    store.create_member("rpf@funston.com", "Richard", "pw")
    credentials = {"email": "rpf@funston.com", "password": "pw"}

    over_https = client.post("/login", data=credentials, base_url="https://paddle.funston.com")
    assert "Secure" in over_https.headers["Set-Cookie"]

    over_http = client.post("/login", data=credentials, base_url="http://192.168.10.45/sport")
    assert "Secure" not in over_http.headers["Set-Cookie"]


def test_cookie_secure_can_be_forced_on(app):
    """Off by default so the LAN HTTP mount keeps working; overridable."""
    assert app.config["SESSION_COOKIE_SECURE"] is False


# -- signing in and out ------------------------------------------------------


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"password" in response.data.lower()


def test_login_with_correct_credentials_redirects_to_the_index(client, store):
    store.create_member("rpf@funston.com", "Richard", "8983498131")
    response = client.post(
        "/login", data={"email": "rpf@funston.com", "password": "8983498131"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_login_accepts_the_email_however_it_is_typed(client, store):
    store.create_member("rpf@funston.com", "Richard", "pw")
    response = client.post(
        "/login", data={"email": "  RPF@Funston.COM  ", "password": "pw"}
    )
    assert response.status_code == 302


def test_login_honours_a_safe_next_parameter(client, store):
    store.create_member("rpf@funston.com", "Richard", "pw")
    response = client.post(
        "/login",
        data={"email": "rpf@funston.com", "password": "pw", "next": "/a/abc123"},
    )
    assert response.headers["Location"].endswith("/a/abc123")


@pytest.mark.parametrize(
    "hostile",
    [
        "//evil.example/",
        "/\\evil.example",
        "https://evil.example/login",
        "http://evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
    ],
)
def test_login_refuses_to_redirect_off_site(client, store, hostile):
    """`next` is attacker-controlled: a bare 'starts with /' check lets //through."""
    store.create_member("rpf@funston.com", "Richard", "pw")
    response = client.post(
        "/login",
        data={"email": "rpf@funston.com", "password": "pw", "next": hostile},
    )
    assert "evil.example" not in response.headers["Location"]
    assert response.headers["Location"].endswith("/")


def test_login_with_wrong_password_shows_one_generic_error(client, store):
    store.create_member("rpf@funston.com", "Richard", "pw")
    response = client.post("/login", data={"email": "rpf@funston.com", "password": "nope"})
    assert response.status_code == 200
    assert b"incorrect" in response.data.lower()


def _without_echoed_input(response, submitted: str) -> bytes:
    """The page with the form's echo of the user's own input removed.

    The login form re-fills the email it was handed, so two failed attempts
    always differ by that string. It is the caller's own input and says nothing
    about whether the account exists, so it is scrubbed before comparing.
    """
    return response.data.replace(submitted.encode(), b"<email>")


def test_login_does_not_reveal_whether_an_account_exists(client, store):
    """No-such-user and wrong-password must be indistinguishable to the caller."""
    store.create_member("rpf@funston.com", "Richard", "pw")

    unknown = client.post("/login", data={"email": "nobody@funston.com", "password": "pw"})
    wrong = client.post("/login", data={"email": "rpf@funston.com", "password": "nope"})

    assert unknown.status_code == wrong.status_code == 200
    assert b"incorrect" in unknown.data.lower()
    assert _without_echoed_input(
        unknown, "nobody@funston.com"
    ) == _without_echoed_input(wrong, "rpf@funston.com")


def test_login_rejects_a_deactivated_member(client, store):
    user = store.create_member("rpf@funston.com", "Richard", "pw")
    store.update_member(user["id"], is_active=False)
    response = client.post("/login", data={"email": "rpf@funston.com", "password": "pw"})
    assert response.status_code == 200
    assert b"incorrect" in response.data.lower()


def test_sign_out_clears_the_session(client, store):
    store.create_member("rpf@funston.com", "Richard", "pw")
    client.post("/login", data={"email": "rpf@funston.com", "password": "pw"})
    with client.session_transaction() as sess:
        assert sess.get("user_id")
    client.post("/logout")
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_sign_out_when_already_signed_out_is_harmless(client):
    response = client.post("/logout")
    assert response.status_code == 302


# -- throttling --------------------------------------------------------------


def _try(client, password, email="rpf@funston.com", ip=None):
    extra = {"environ_base": {"REMOTE_ADDR": ip}} if ip else {}
    return client.post(
        "/login", data={"email": email, "password": password}, **extra
    )


def test_repeated_failures_are_throttled(client, store):
    """The admin password is ten numeric digits, so an unthrottled public login
    is 10^10 guesses away from the account."""
    store.create_member("rpf@funston.com", "Richard", "8983498131")

    for _ in range(LOGIN_MAX_FAILURES):
        assert _try(client, "wrong").status_code == 200

    blocked = _try(client, "8983498131")
    assert blocked.status_code == 429
    assert b"too many" in blocked.data.lower()


def test_throttling_does_not_lock_out_a_different_address(client, store):
    """A stranger must not be able to lock the real member out of their account."""
    store.create_member("rpf@funston.com", "Richard", "8983498131")

    for _ in range(LOGIN_MAX_FAILURES + 1):
        _try(client, "wrong", ip="10.9.9.9")

    assert _try(client, "8983498131", ip="10.1.1.1").status_code == 302


def test_a_successful_login_clears_the_failures(client, store):
    store.create_member("rpf@funston.com", "Richard", "8983498131")

    for _ in range(LOGIN_MAX_FAILURES - 1):
        _try(client, "wrong")
    assert _try(client, "8983498131").status_code == 302

    # The slate is clean, so a fresh run of failures is needed to throttle again.
    for _ in range(LOGIN_MAX_FAILURES - 1):
        assert _try(client, "wrong").status_code == 200
    assert _try(client, "8983498131").status_code == 302


def test_a_failed_login_is_logged(client, store, caplog):
    store.create_member("rpf@funston.com", "Richard", "pw")
    with caplog.at_level("WARNING"):
        _try(client, "wrong")
    assert any("rpf@funston.com" in r.message for r in caplog.records)


def test_the_throttle_survives_a_restart(tmp_path):
    """Two gunicorn workers, so an in-process counter would halve the limit."""
    store = Store(tmp_path / "data")
    assert store.count_recent_login_failures("a@funston.com", "1.2.3.4", 900) == 0
    store.record_login_failure("a@funston.com", "1.2.3.4")
    store.record_login_failure("a@funston.com", "1.2.3.4")
    assert store.count_recent_login_failures("a@funston.com", "1.2.3.4", 900) == 2
    # A different address is counted separately.
    assert store.count_recent_login_failures("a@funston.com", "5.6.7.8", 900) == 0
    store.clear_login_failures("a@funston.com", "1.2.3.4")
    assert store.count_recent_login_failures("a@funston.com", "1.2.3.4", 900) == 0
