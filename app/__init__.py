"""Application factory for the sport data analyzer."""

from __future__ import annotations

from datetime import timedelta

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .storage import Store

__all__ = ["create_app"]


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

    # Adopt any activity that has no paddler at all. Unconditional and
    # idempotent, on every startup: an unowned session is invisible to every
    # account, which is indistinguishable from having lost it. Gating this on
    # an empty users table would skip it on the rollout path, where the first
    # account is created by the CLI and the table is therefore no longer empty.
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
