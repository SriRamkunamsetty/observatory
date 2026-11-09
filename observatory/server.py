"""Flask app: OTLP/JSON ingestion, failure clustering dashboard, regression-test generator."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing

from flask import Flask, Response, abort, g, jsonify, render_template, request

from . import core
from .security import init_security, require_key

SCHEMA = """CREATE TABLE IF NOT EXISTS traces(id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, prompt TEXT,
  response TEXT, status TEXT NOT NULL, latency_ms REAL NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_status ON traces(status);
CREATE INDEX IF NOT EXISTS idx_latency ON traces(latency_ms);
CREATE INDEX IF NOT EXISTS idx_ts ON traces(ts);"""
MAX_SPANS = 5000          # LLM spans accepted per request
CLUSTER_LIMIT = 2000      # most recent failing traces considered for clustering (bounds CPU and memory)
CACHE_SIZE = 8            # cached clusterings (per worker)


def create_app(db_path: str | None = None, test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or os.path.join(os.environ.get("DATA_DIR", "./data"), "observatory.db")
    app.config["MAX_CONTENT_LENGTH"] = 5_000_000
    if test_config:
        app.config.update(test_config)
    init_security(app)
    os.makedirs(os.path.dirname(os.path.abspath(app.config["DB_PATH"])), exist_ok=True)
    with closing(sqlite3.connect(app.config["DB_PATH"])) as conn:
        conn.executescript(SCHEMA)
    cache: dict[tuple, list[list[core.Trace]]] = {}

    def db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DB_PATH"])
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def threshold() -> float:
        try:
            return min(1.0, max(0.1, float(request.args.get("threshold", 0.5))))
        except ValueError:
            abort(400)

    def clusters_for(thr: float) -> list[list[core.Trace]]:
        """Cluster recent failures; results are cached until the set of failures changes."""
        count, newest = db().execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM traces WHERE status != 'ok'").fetchone()
        key = (count, newest, thr, CLUSTER_LIMIT)
        if key not in cache:
            rows = db().execute(
                "SELECT trace_id, prompt, response, status, latency_ms FROM traces "
                "WHERE status != 'ok' ORDER BY id DESC LIMIT ?", (CLUSTER_LIMIT,)).fetchall()
            traces = [core.Trace(r["trace_id"], r["prompt"], r["response"], r["status"], r["latency_ms"])
                      for r in reversed(rows)]
            if len(cache) >= CACHE_SIZE:
                cache.pop(next(iter(cache)))
            cache[key] = core.cluster(traces, thr)
        return cache[key]

    def summarize(groups: list[list[core.Trace]]) -> list[dict]:
        return [{"label": core.label(g_), "count": len(g_), "status": g_[0].status,
                 "sample_prompt": g_[0].prompt, "sample_response": g_[0].response,
                 "trace_ids": [t.id for t in g_][:20]} for g_ in groups]

    def stats() -> dict:
        """Aggregates computed in SQL: no trace rows are loaded into Python."""
        total, failures = db().execute(
            "SELECT COUNT(*), COALESCE(SUM(status != 'ok'), 0) FROM traces").fetchone()

        def pct(p: int) -> float:
            if not total:
                return 0.0
            offset = max(1, -(-total * p // 100)) - 1  # nearest-rank, same as core.percentile
            return float(db().execute(
                "SELECT latency_ms FROM traces ORDER BY latency_ms LIMIT 1 OFFSET ?", (offset,)).fetchone()[0])

        return {"total": total, "failures": failures, "failure_rate": failures / total if total else 0.0,
                "p50_ms": pct(50), "p95_ms": pct(95)}

    def purge_old() -> None:
        days = float(os.environ.get("TRACE_RETENTION_DAYS", "30"))
        if days > 0:
            db().execute("DELETE FROM traces WHERE ts < ?", (time.time() - days * 86400,))

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    @app.get("/metrics")
    def metrics():
        s = stats()
        body = (f"observatory_traces_total {s['total']}\nobservatory_failures_total {s['failures']}\n"
                f"observatory_failure_rate {s['failure_rate']:.4f}\nobservatory_latency_p95_ms {s['p95_ms']:.1f}\n")
        return Response(body, mimetype="text/plain")

    @app.post("/v1/traces")
    @require_key
    def ingest():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or "resourceSpans" not in payload:
            abort(400)
        traces = core.parse_otlp(payload)
        if len(traces) > MAX_SPANS:
            abort(413)
        now = time.time()
        db().executemany(
            "INSERT INTO traces(trace_id, prompt, response, status, latency_ms, ts) VALUES (?,?,?,?,?,?)",
            [(t.id, t.prompt, t.response, t.status, t.latency_ms, now) for t in traces])
        purge_old()
        db().commit()
        return jsonify(accepted=len(traces)), 200

    @app.get("/api/stats")
    def api_stats():
        return jsonify(stats())

    @app.get("/api/clusters")
    def api_clusters():
        return jsonify(summarize(clusters_for(threshold())))

    @app.get("/api/regression.py")
    def api_regression():
        code = core.render_pytest(core.to_cases(clusters_for(threshold())))
        return Response(code, mimetype="text/x-python",
                        headers={"Content-Disposition": "attachment; filename=test_regressions.py"})

    @app.get("/")
    def index():
        recent = db().execute("SELECT * FROM traces WHERE status != 'ok' ORDER BY id DESC LIMIT 8").fetchall()
        return render_template("index.html", s=stats(), clusters=summarize(clusters_for(threshold()))[:5],
                               recent=recent)

    @app.get("/clusters")
    def clusters_page():
        return render_template("clusters.html", clusters=summarize(clusters_for(threshold())))

    @app.get("/regression")
    def regression_page():
        return render_template("regression.html",
                               code=core.render_pytest(core.to_cases(clusters_for(threshold()))))

    return app
