"""Authentication: password hashing, the secret key, and the signed-cookie session.

Kept separate from :mod:`app.storage` so the store can hash a password without
owning the auth concern. Nothing here imports the store at module level - it
reaches the store through the app, which keeps the import graph one-way.

The session cookie carries the paddler's id and a ``session_epoch``. Every
request re-reads the row, so a deactivated account, a deleted account, or a
password changed since the cookie was issued all read as signed out.
"""

from __future__ import annotations

import functools
import os
import re
import secrets
from pathlib import Path
from typing import Any, Callable

from flask import abort, current_app, redirect, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.security import check_password_hash, generate_password_hash

__all__ = [
    "hash_password",
    "check_password",
    "load_secret_key",
    "current_paddler",
    "login_paddler",
    "logout_paddler",
    "login_required",
    "admin_required",
    "usable_email",
]

#: Enough of a check that a typo cannot quietly become an account. NPM's own
#: add-user form silently refuses an unparseable address, which is exactly the
#: failure this guards against.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def usable_email(candidate: str) -> bool:
    return bool(_EMAIL_PATTERN.match((candidate or "").strip()))


class SchemeAwareSession(SecureCookieSessionInterface):
    """Mark the session cookie ``Secure`` exactly when the request was HTTPS.

    One gunicorn process serves the public hostname over HTTPS *and* the plain
    HTTP LAN path mount, so a static flag cannot serve both: ``Secure`` on means
    a browser never sends the cookie back over the LAN route and sign-in there
    breaks; ``Secure`` off means a 12-hour session cookie travels in the clear
    on the public host.

    Following the scheme gives each route what it needs with no configuration
    and no way to get it wrong. ``SESSION_COOKIE_SECURE = True`` still forces it
    on for a deployment that is HTTPS-only.
    """

    def get_cookie_secure(self, app) -> bool:  # type: ignore[override]
        if app.config.get("SESSION_COOKIE_SECURE"):
            return True
        return bool(request.is_secure)

#: Name of the key file inside the data directory.
SECRET_KEY_FILENAME = "secret_key"

#: How long a session lasts. Flask re-issues the cookie on each request while
#: SESSION_REFRESH_EACH_REQUEST is true, so this slides.
SESSION_HOURS = 12

#: Failed sign-ins allowed from one address against one account inside the
#: window before that pair is throttled.
#:
#: Keyed on (email, address) rather than either alone: keying on address alone
#: lets a shared NAT lock out everyone behind it, and keying on email alone
#: lets a stranger lock a known paddler out of their own account.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60


# -- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash a password for storage.

    ``werkzeug`` uses scrypt with a per-password salt. The plaintext is never
    written anywhere.
    """
    return generate_password_hash(password)


def check_password(password: str, password_hash: str) -> bool:
    """True when ``password`` matches the stored hash.

    An unreadable hash returns False rather than raising, so one damaged row
    fails a login instead of returning a 500.
    """
    return check_password_hash(password_hash, password)


# -- the secret key ----------------------------------------------------------


def load_secret_key(data_dir: Path | str) -> str:
    """The key session cookies are signed with.

    ``SDA_SECRET_KEY`` wins when set. Otherwise a key is created once and
    persisted, so it survives a restart and every worker agrees on it.

    The create is exclusive and a collision **re-reads** rather than
    regenerating. ``wsgi.py`` calls the app factory at import time and the unit
    runs gunicorn with ``--workers 2`` and no ``--preload``, so two processes
    run this independently on a cold start. Regenerating on collision would
    leave one worker signing cookies with a key that no longer matches disk, and
    roughly half of all sign-ins would then fail on the next request - a bug
    that cannot reproduce in tests, which build a single in-process app.
    """
    from_env = os.environ.get("SDA_SECRET_KEY")
    if from_env:
        return from_env

    path = Path(data_dir) / SECRET_KEY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Another worker won the race. Its key is the one on disk and therefore
        # the one everyone must use.
        return path.read_text(encoding="utf-8").strip()

    key = secrets.token_urlsafe(48)
    with os.fdopen(fd, "w") as handle:
        handle.write(key)
    return key


# -- the session -------------------------------------------------------------


def current_paddler() -> dict[str, Any] | None:
    """The signed-in paddler, or ``None``.

    Re-reads the row every request rather than trusting the cookie, so anything
    that happened to the account since the cookie was issued takes effect on the
    very next request.
    """
    store = current_app.extensions.get("store")
    if store is None:
        return None

    user_id = session.get("user_id")
    epoch = session.get("epoch")
    if not user_id or epoch is None:
        return None

    user = store.get_paddler(user_id)
    if user is None or not user["is_active"]:
        return None
    if int(user["session_epoch"]) != int(epoch):
        return None
    return user


def login_paddler(user: dict[str, Any]) -> None:
    """Sign a paddler in for :data:`SESSION_HOURS`."""
    session.clear()
    session["user_id"] = user["id"]
    session["epoch"] = int(user["session_epoch"])
    session.permanent = True


def logout_paddler() -> None:
    session.clear()


# -- gates -------------------------------------------------------------------


def _sign_in_target() -> str:
    return url_for("sda.login", next=request.full_path)


def login_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_paddler() is None:
            return redirect(_sign_in_target())
        return view(*args, **kwargs)

    return wrapped


def admin_required(view: Callable) -> Callable:
    """Admin only.

    Resolves the signed-in paddler *first*: an anonymous request gets the same
    redirect as :func:`login_required` rather than a 403. Answering 403 would
    tell a stranger the route exists.
    """

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_paddler()
        if user is None:
            return redirect(_sign_in_target())
        if not user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)

    return wrapped
