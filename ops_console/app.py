import os

from flask import Flask, Response, request
from werkzeug.middleware.proxy_fix import ProxyFix

import db
from .routes import ops_bp


def _check_auth(username, password):
    expected_user = os.getenv("OPS_CONSOLE_USERNAME")
    expected_pass = os.getenv("OPS_CONSOLE_PASSWORD")
    if not expected_user or not expected_pass:
        return True
    return username == expected_user and password == expected_pass


def _auth_required():
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Student Bot Ops Console"'},
    )


class PrefixMiddleware:
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = prefix.rstrip("/")

    def __call__(self, environ, start_response):
        if not self.prefix:
            return self.app(environ, start_response)

        script_name = environ.get("HTTP_X_FORWARDED_PREFIX") or self.prefix
        script_name = script_name.rstrip("/") or "/"
        path_info = environ.get("PATH_INFO", "")
        if path_info.startswith(script_name):
            environ["PATH_INFO"] = path_info[len(script_name):] or "/"
        environ["SCRIPT_NAME"] = script_name
        return self.app(environ, start_response)


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "student-bot-ops-console"
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    url_prefix = os.getenv("OPS_CONSOLE_URL_PREFIX", "").strip()
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, url_prefix)
    db.init()
    db.record_system_health("ops_console", "ok", "app initialized")

    @app.before_request
    def enforce_basic_auth():
        auth = request.authorization
        if _check_auth(auth.username if auth else None, auth.password if auth else None):
            return None
        return _auth_required()

    app.register_blueprint(ops_bp)
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8090, debug=False)
