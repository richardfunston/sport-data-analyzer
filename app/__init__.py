"""Application factory for the sport data analyzer."""

from __future__ import annotations

import os
from datetime import timedelta

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .storage import Store

__all__ = ["create_app"]


def _bootstrap_first_admin(store: Store, app: Flask) -> None:
    """Create the first admin from the environment when there are no paddlers.

    The first account's password is deliberately not in the repository, so a
    fresh clone takes it from the environment. This fires **only** when the
    users table is empty, so it can never resurrect an account or quietly reset
    a password on a restart - which is why it is safe to run on every startup.

    Silently does nothing unless all three variables are set and the email is
    usable; a typo must not create an account nobody can sign in as.
    """
    if store.list_paddlers():
        return

    email = (os.environ.get("SDA_INITIAL_PADDLER_EMAIL") or "").strip()
    name = (os.environ.get("SDA_INITIAL_PADDLER_NAME") or "").strip()
    password = os.environ.get("SDA_INITIAL_PADDLER_PASSWORD") or ""
    if not (email and name and password):
        return

    from .auth import usable_email

    if not usable_email(email):
        app.logger.warning(
            "SDA_INITIAL_PADDLER_EMAIL is not a usable address; skipping the "
            "first-paddler bootstrap"
        )
        return

    store.create_paddler(email, name, password, is_admin=True)
    app.logger.info("created the first paddler %s from the environment", email)


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)
    app.config["MAX_CONTENT_LENGTH"] = config_object.MAX_CONTENT_LENGTH

    # Behind Caddy's handle_path the prefix is stripped from PATH_INFO and
    # forwarded in X-Forwarded-Prefix, so url_for() emits correct links.
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

    store = Store(config_object.DATA_DIR)
    app.extensions["store"] = store

    # Session cookies are signed with this, so it must be a real secret and must
    # be identical across gunicorn workers. See load_secret_key for the race it
    # guards against.
    from .auth import SESSION_HOURS, load_secret_key

    app.config["SECRET_KEY"] = load_secret_key(config_object.DATA_DIR)
    app.permanent_session_lifetime = timedelta(hours=SESSION_HOURS)
    # Flask's default emits no SameSite attribute at all, leaving cross-site
    # cookie behaviour to whichever browser is asking. Lax is what actually
    # stops a cross-site form post from carrying the session.
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # Secure follows the request scheme: the public host is HTTPS, the LAN mount
    # is plain HTTP, and one process serves both. See SchemeAwareSession.
    from .auth import SchemeAwareSession

    app.session_interface = SchemeAwareSession()

    # Adopt any activity that has no paddler at all. Unconditional and
    # idempotent, on every startup: an unowned session is invisible to every
    # account, which is indistinguishable from having lost it. Gating this on
    # an empty users table would skip it on the rollout path, where the first
    # account is created by the CLI and the table is therefore no longer empty.
    _bootstrap_first_admin(store, app)
    admin = store.first_admin()
    if admin is not None:
        store.backfill_paddlers(admin["id"])

    from .formatting import register_filters

    register_filters(app)

    # Every template can ask who is signed in, so the header can name them.
    from .auth import current_paddler

    app.jinja_env.globals["current_paddler"] = current_paddler

    from .routes import bp

    app.register_blueprint(bp)
    return app
