"""Cross-cutting security and robustness tests (auth edge cases, headers, secrets, memory bounds)."""
import os
import stat
import tempfile
import time
import unittest
from unittest import mock

from observatory import security
from observatory.server import create_app


def protected(client, headers):
    return client.post("/v1/traces", json={"resourceSpans": []}, headers=headers)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"API_KEY": "test-key"})
        self.env.start()
        os.environ.pop("SECRET_KEY", None)
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.env.stop()

    def app(self, name="s.db", **cfg):
        return create_app(os.path.join(self.tmp, name), {"RATE_LIMIT": 10000, **cfg})

    def test_non_ascii_api_key_is_rejected_not_crashed(self):
        self.assertEqual(protected(self.app().test_client(), {"X-API-Key": "é"}).status_code, 401)

    def test_valid_key_accepted(self):
        self.assertIn(protected(self.app().test_client(), {"X-API-Key": "test-key"}).status_code, (200, 201))

    def test_unexpected_error_is_generic_500(self):
        app = self.app()
        app.route("/boom")(lambda: 1 / 0)
        with self.assertLogs(app.logger, "ERROR"):
            resp = app.test_client().get("/boom")
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn(b"ZeroDivision", resp.data)
        self.assertNotIn(b"Traceback", resp.data)

    def test_static_assets_cacheable_pages_not(self):
        client = self.app().test_client()
        with client.get("/static/style.css") as static:
            self.assertIn("max-age", static.headers["Cache-Control"])
        self.assertEqual(client.get("/healthz").headers["Cache-Control"], "no-store")

    def test_csp_forbids_inline_scripts_and_styles(self):
        csp = self.app().test_client().get("/healthz").headers["Content-Security-Policy"]
        self.assertNotIn("unsafe-inline", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_secret_key_persisted_shared_between_workers_and_private(self):
        a, b = self.app(), self.app()
        self.assertEqual(a.secret_key, b.secret_key)
        mode = stat.S_IMODE(os.stat(os.path.join(self.tmp, ".secret_key")).st_mode)
        if os.name == "posix":
            self.assertEqual(mode, 0o600)
        else:
            self.assertIn(mode, (0o600, 0o666))

    def test_env_secret_overrides_file(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "from-env"}):
            self.assertEqual(self.app("e.db").secret_key, "from-env")

    def test_rate_limiter_memory_is_bounded(self):
        with mock.patch.object(security, "MAX_TRACKED_CLIENTS", 5):
            app = self.app()
            client = app.test_client()
            for i in range(30):
                client.get("/healthz", environ_overrides={"REMOTE_ADDR": f"10.0.0.{i}"})
            self.assertLessEqual(len(app.extensions["rate_clients"]), 6)

    def test_hsts_only_when_cookies_are_secure(self):
        self.assertNotIn("Strict-Transport-Security", self.app().test_client().get("/healthz").headers)
        with mock.patch.dict(os.environ, {"COOKIE_SECURE": "1"}):
            resp = self.app("h.db").test_client().get("/healthz")
        self.assertIn("max-age", resp.headers["Strict-Transport-Security"])

    def test_trust_proxy_uses_forwarded_client_ip(self):
        with mock.patch.dict(os.environ, {"TRUST_PROXY": "1"}):
            client = self.app("p.db", RATE_LIMIT=1).test_client()
            codes = [client.get("/healthz", headers={"X-Forwarded-For": ip}).status_code
                     for ip in ("1.1.1.1", "2.2.2.2")]
        self.assertEqual(codes, [200, 200])

    def test_secret_loader_edge_cases(self):
        path = os.path.join(self.tmp, "race.key")
        open(path, "w").close()  # file exists but another worker has not finished writing

        def finish_write(_seconds):
            with open(path, "w") as fh:
                fh.write("abc123")

        with mock.patch.object(security.time, "sleep", side_effect=finish_write):
            self.assertEqual(security._load_secret(path), "abc123")
        ephemeral = security._load_secret("/nonexistent-dir/.secret_key")  # read-only location
        self.assertRegex(ephemeral, r"^[0-9a-f]{64}$")

    def test_rate_window_resets_and_stale_entries_are_pruned(self):
        app = self.app(RATE_LIMIT=2)
        clients = app.extensions["rate_clients"]
        client = app.test_client()
        clients["127.0.0.1"] = (time.time() - 120, 99)  # expired window must reset
        self.assertEqual(client.get("/healthz").status_code, 200)
        with mock.patch.object(security, "MAX_TRACKED_CLIENTS", 2):
            for i in range(3):
                clients[f"old{i}"] = (time.time() - 120, 1)
            client.get("/healthz")
        self.assertFalse(any(k.startswith("old") for k in clients))

    def test_default_request_size_limit_is_applied(self):
        from flask import Flask
        bare = Flask("bare")
        bare.config["DB_PATH"] = os.path.join(self.tmp, "b.db")
        security.init_security(bare)
        self.assertEqual(bare.config["MAX_CONTENT_LENGTH"], 1_000_000)


if __name__ == "__main__":
    unittest.main()
