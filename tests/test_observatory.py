import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from observatory import core
from observatory.server import create_app

KEY = {"X-API-Key": "test-key"}


def span(tid, prompt, completion, error=False, ms=100, status=None):
    attrs = [{"key": "gen_ai.prompt", "value": {"stringValue": prompt}},
             {"key": "gen_ai.completion", "value": {"stringValue": completion}}]
    if status:
        attrs.append({"key": "quality.status", "value": {"stringValue": status}})
    return {"traceId": tid, "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": str(1_000_000_000 + ms * 1_000_000),
            "status": {"code": 2 if error else 1}, "attributes": attrs}


def otlp(*spans):
    return {"resourceSpans": [{"scopeSpans": [{"spans": list(spans)}]}]}


class CoreTests(unittest.TestCase):
    def test_redaction(self):
        out = core.redact("mail bob@corp.com or call +1 (555) 123-4567")
        self.assertNotIn("bob@corp.com", out)
        self.assertNotIn("555", out)

    def test_parse_otlp(self):
        ts = core.parse_otlp(otlp(span("t1", "hi a@b.io", "x", error=True, ms=12),
                                  span("t2", "q", "a", status="weird"),
                                  {"traceId": "skip", "attributes": []}))
        self.assertEqual(len(ts), 2)
        self.assertEqual((ts[0].status, ts[0].latency_ms), ("error", 12.0))
        self.assertNotIn("a@b.io", ts[0].prompt)
        self.assertEqual(ts[1].status, "flagged")
        self.assertEqual(core.parse_otlp({}), [])

    def test_percentile(self):
        self.assertEqual(core.percentile([], 50), 0.0)
        self.assertEqual(core.percentile([10, 20, 30, 40], 50), 20.0)
        self.assertEqual(core.percentile(list(range(1, 101)), 95), 95.0)

    def test_clustering(self):
        T = core.Trace
        ts = [T("1", "reset my password", "timeout error", "error"),
              T("2", "reset my password please", "timeout error", "error"),
              T("3", "translate hello french", "wrong answer", "flagged"),
              T("4", "reset my password", "fine", "ok")]
        groups = core.cluster(ts)
        self.assertEqual([len(g) for g in groups], [2, 1])
        self.assertEqual(core.jaccard(set(), set()), 0.0)
        self.assertIn("password", core.label(groups[0]))

    def test_generated_pytest_is_valid_python(self):
        T = core.Trace
        cases = core.to_cases(core.cluster([T("1", "it's a \"quote\"", "err", "error")]))
        ns: dict = {}
        with mock.patch.dict("sys.modules", {"pytest": mock.MagicMock()}):
            exec(compile(core.render_pytest(cases), "gen", "exec"), ns)
        self.assertEqual(ns["CASES"][0]["prompt"], "it's a \"quote\"")
        self.assertEqual(core.to_cases([], 1), [])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"API_KEY": "test-key"})
        self.env.start()
        self.app = create_app(os.path.join(tempfile.mkdtemp(), "t.db"), {"RATE_LIMIT": 10000})
        self.c = self.app.test_client()

    def tearDown(self):
        self.env.stop()

    def ingest(self, *spans, headers=KEY):
        return self.c.post("/v1/traces", json=otlp(*spans), headers=headers)

    def seed(self):
        self.ingest(span("1", "reset my password", "timeout error", error=True, ms=900),
                    span("2", "reset my password please", "timeout error", error=True, ms=800),
                    span("3", "capital of france", "Paris", ms=50),
                    span("4", "cite a paper", "fake citation", status="flagged", ms=300))

    def test_ingest_and_stats(self):
        self.seed()
        s = self.c.get("/api/stats").get_json()
        self.assertEqual((s["total"], s["failures"]), (4, 3))
        self.assertAlmostEqual(s["failure_rate"], 0.75)

    def test_clusters_api_and_threshold(self):
        self.seed()
        cl = self.c.get("/api/clusters").get_json()
        self.assertEqual([c["count"] for c in cl], [2, 1])
        self.assertEqual(self.c.get("/api/clusters?threshold=abc").status_code, 400)
        self.assertEqual(self.c.get("/api/clusters?threshold=5").status_code, 200)

    def test_regression_download_compiles(self):
        self.seed()
        r = self.c.get("/api/regression.py")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        compile(r.data.decode(), "dl", "exec")

        r_adv = self.c.get("/api/regression.py?suite=advanced")
        self.assertIn("attachment", r_adv.headers["Content-Disposition"])
        compile(r_adv.data.decode(), "dl_adv", "exec")

    def test_taxonomy_and_drift_api(self):
        self.seed()
        tax = self.c.get("/api/taxonomy").get_json()
        self.assertIsInstance(tax, dict)
        self.assertIn("hallucination", tax)

        drift = self.c.get("/api/drift").get_json()
        self.assertIn("status", drift)
        self.assertIn("centroid_drift", drift)

    def test_semantic_clusters_api(self):
        self.seed()
        sem = self.c.get("/api/clusters/semantic").get_json()
        self.assertIsInstance(sem, list)
        self.assertGreater(len(sem), 0)
        self.assertIn("taxonomy", sem[0])
        self.assertIn("x", sem[0])
        self.assertIn("y", sem[0])

    def test_pages_render(self):
        self.seed()
        for path in ("/", "/clusters", "/regression"):
            self.assertEqual(self.c.get(path).status_code, 200, path)
        self.assertIn(b"password", self.c.get("/clusters").data)

    def test_empty_state(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/api/stats").get_json()["total"], 0)

    def test_auth_and_validation(self):
        self.assertEqual(self.ingest(span("1", "p", "r"), headers={}).status_code, 401)
        with mock.patch.dict(os.environ, {"API_KEY": ""}):
            self.assertEqual(self.ingest(span("1", "p", "r")).status_code, 503)
        self.assertEqual(self.c.post("/v1/traces", json={"x": 1}, headers=KEY).status_code, 400)
        self.assertEqual(self.c.post("/v1/traces", data="no", headers=KEY).status_code, 400)

    def test_pii_redacted_at_rest(self):
        self.ingest(span("1", "email me at jane@corp.com", "ok", error=True))
        self.assertNotIn("jane@corp.com", self.c.get("/api/clusters").get_data(as_text=True))

    def test_span_cap(self):
        with mock.patch("observatory.server.MAX_SPANS", 2):
            self.assertEqual(self.ingest(*[span(str(i), "p", "r") for i in range(3)]).status_code, 413)

    def test_xss_escaped_and_headers(self):
        self.ingest(span("1", "<script>alert(1)</script>", "bad", error=True))
        for path in ("/", "/clusters", "/regression"):
            page = self.c.get(path)
            self.assertNotIn(b"<script>alert(1)", page.data, path)
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")

    def test_rate_limit_health_metrics(self):
        self.seed()
        m = self.c.get("/metrics").data.decode()
        self.assertIn("observatory_traces_total 4", m)
        app = create_app(os.path.join(tempfile.mkdtemp(), "r.db"), {"RATE_LIMIT": 2})
        codes = [app.test_client().get("/healthz").status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class RobustnessAndEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"API_KEY": "test-key"})
        self.env.start()
        self.app = create_app(os.path.join(tempfile.mkdtemp(), "t.db"), {"RATE_LIMIT": 10000})
        self.c = self.app.test_client()

    def tearDown(self):
        self.env.stop()

    def ingest(self, *spans):
        return self.c.post("/v1/traces", json=otlp(*spans), headers=KEY)

    def test_malformed_otlp_never_crashes(self):
        bad_payloads = [
            {"resourceSpans": {"a": 1}}, {"resourceSpans": ["x"]}, {"resourceSpans": [{"scopeSpans": "x"}]},
            {"resourceSpans": [{"scopeSpans": [{"spans": [None, 5, "s"]}]}]},
            {"resourceSpans": [{"scopeSpans": [{"spans": [{"startTimeUnixNano": "abc", "attributes": [
                {"key": "gen_ai.prompt", "value": {"stringValue": "p"}}]}]}]}]},
            {"resourceSpans": [{"scopeSpans": [{"spans": [{"attributes": [
                None, {"key": 5, "value": "str"}, {"key": "gen_ai.prompt", "value": {"arrayValue": {"values": []}}}]}]}]}]},
        ]
        for body in bad_payloads:
            self.assertEqual(self.c.post("/v1/traces", json=body, headers=KEY).status_code, 200, body)
        self.assertEqual(core.parse_otlp(None), [])

    def test_sql_stats_match_reference_percentiles(self):
        latencies = [10, 20, 30, 40, 50, 60, 70, 80, 90, 1000]
        self.ingest(*[span(str(i), "p", "r", ms=ms) for i, ms in enumerate(latencies)])
        s = self.c.get("/api/stats").get_json()
        self.assertEqual(s["p50_ms"], core.percentile([float(x) for x in latencies], 50))
        self.assertEqual(s["p95_ms"], core.percentile([float(x) for x in latencies], 95))

    def test_clustering_is_cached_and_invalidated_on_new_failures(self):
        self.ingest(span("1", "reset my password", "timeout error", error=True))
        with mock.patch("observatory.server.core.cluster", wraps=core.cluster) as spy:
            self.assertEqual(len(self.c.get("/api/clusters").get_json()), 1)
            self.c.get("/clusters")
            self.c.get("/api/regression.py")
            self.assertEqual(spy.call_count, 1)  # three reads, one computation
            self.ingest(span("2", "translate hello french", "wrong answer", error=True))
            self.assertEqual(len(self.c.get("/api/clusters").get_json()), 2)
            self.assertEqual(spy.call_count, 2)

    def test_cluster_input_is_bounded(self):
        with mock.patch("observatory.server.CLUSTER_LIMIT", 3):
            self.ingest(*[span(str(i), f"unique{i} words{i}", f"fail{i}", error=True) for i in range(6)])
            groups = self.c.get("/api/clusters").get_json()
        self.assertEqual(sum(g["count"] for g in groups), 3)

    def test_retention_purges_old_traces_on_ingest(self):
        self.ingest(span("new0", "p", "r"))
        conn = sqlite3.connect(self.app.config["DB_PATH"])
        conn.execute("INSERT INTO traces(trace_id, prompt, response, status, latency_ms, ts) VALUES ('old','p','r','ok',1,?)",
                     (time.time() - 40 * 86400,))
        conn.commit()
        conn.close()
        self.assertEqual(self.c.get("/api/stats").get_json()["total"], 2)
        self.ingest(span("new1", "p", "r"))
        self.assertEqual(self.c.get("/api/stats").get_json()["total"], 2)  # old row purged, 2 new remain
        with mock.patch.dict(os.environ, {"TRACE_RETENTION_DAYS": "0"}):  # 0 disables retention
            self.ingest(span("new2", "p", "r"))
        self.assertEqual(self.c.get("/api/stats").get_json()["total"], 3)


if __name__ == "__main__":
    unittest.main()
