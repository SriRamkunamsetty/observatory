"""Shared security helpers: headers, rate limiting, API-key auth, CSRF, error handling."""
from __future__ import annotations

import hmac
import os
import secrets
import time
from functools import wraps

from flask import abort, current_app, jsonify, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

CODES = (400, 401, 403, 404, 405, 413, 429, 500, 502, 503, 504)
MAX_TRACKED_CLIENTS = 10_000
CSP = ("default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")


def safe_equal(a: str, b: str) -> bool:
    """Constant-time comparison that tolerates non-ASCII input (compare_digest on str would raise)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _load_secret(path: str) -> str:
    """Secret shared by all gunicorn workers and surviving restarts; created atomically, mode 0600."""
    for _ in range(40):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            with open(path, encoding="ascii") as fh:
                value = fh.read().strip()
            if value:
                return value
            time.sleep(0.05)  # another worker is mid-write
            continue
        except OSError:
            break  # read-only location: fall back to an ephemeral key
        key = secrets.token_hex(32)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(key)
        return key
    return secrets.token_hex(32)


def init_security(app) -> None:
    """Wire headers, size limit, rate limiting, cookies and error handlers into a Flask app."""
    # Flask pre-defines these keys (as None/False), so setdefault() would silently do nothing.
    if not app.config.get("MAX_CONTENT_LENGTH"):
        app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
    data_dir = os.path.dirname(os.path.abspath(app.config["DB_PATH"]))
    app.secret_key = os.environ.get("SECRET_KEY") or _load_secret(os.path.join(data_dir, ".secret_key"))
    if os.environ.get("TRUST_PROXY") == "1":  # only enable behind a proxy you control
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    clients: dict[str, tuple[float, int]] = {}
    app.extensions["rate_clients"] = clients  # exposed for tests / diagnostics

    @app.before_request
    def _rate_limit():
        limit = app.config.get("RATE_LIMIT") or int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))
        ip, now = request.remote_addr or "?", time.time()
        if len(clients) > MAX_TRACKED_CLIENTS:  # bound memory: drop expired windows, then everything
            for stale in [k for k, (s, _) in clients.items() if now - s >= 60]:
                del clients[stale]
            if len(clients) > MAX_TRACKED_CLIENTS:
                clients.clear()
        start, count = clients.get(ip, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        clients[ip] = (start, count + 1)
        if count + 1 > limit:
            abort(429)

    @app.after_request
    def _headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "public, max-age=3600" if request.endpoint == "static" else "no-store"
        if app.config["SESSION_COOKIE_SECURE"]:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    def _error(err):
        if request.path.startswith(("/api", "/v1")):
            return jsonify(error=err.name), err.code
        return f"{err.code} {err.name}", err.code

    for code in CODES:
        app.register_error_handler(code, _error)
    app.jinja_env.globals["csrf_token"] = csrf_token


def require_key(fn):
    """Write endpoints are disabled (503) unless API_KEY is configured; then compared in constant time."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = os.environ.get("API_KEY")
        if not key:
            abort(503)
        if not safe_equal(request.headers.get("X-API-Key", ""), key):
            current_app.logger.warning("auth failure from %s on %s", request.remote_addr, request.path)
            abort(401)
        return fn(*args, **kwargs)

    return wrapper


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf() -> None:
    if not safe_equal(request.form.get("_csrf", ""), session.get("_csrf", "\x00")):
        abort(403)
