"""Application factory for the sport data analyzer."""

from __future__ import annotations

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

    app.extensions["store"] = Store(config_object.DATA_DIR)

    from .formatting import register_filters

    register_filters(app)

    from .routes import bp

    app.register_blueprint(bp)
    return app
