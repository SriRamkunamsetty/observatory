"""Flask app: OTLP/JSON ingestion, failure clustering dashboard, regression-test generator."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing

from flask import Flask, Response, abort, g, jsonify, render_template, request

from . import core, ml
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

    def taxonomy_stats() -> dict[str, int]:
        rows = db().execute(
            "SELECT prompt, response, status, latency_ms FROM traces "
            "WHERE status != 'ok' ORDER BY id DESC LIMIT 500"
        ).fetchall()
        counts: dict[str, int] = {cat.value: 0 for cat in ml.FailureTaxonomy}
        for r in rows:
            c = ml.classify_failure(r["prompt"], r["response"], r["status"], r["latency_ms"])
            counts[c.category.value] = counts.get(c.category.value, 0) + 1
        return counts

    def drift_stats() -> dict:
        total = db().execute("SELECT COUNT(*) FROM traces").fetchone()[0]
        if total < 10:
            return ml.DriftReport("STABLE", 0.0, 0.0, 0.0, 0.0, total, 0, "Collecting traffic baseline...").to_dict()
        half = total // 2
        base_rows = db().execute(
            "SELECT trace_id, prompt, response, status, latency_ms FROM traces ORDER BY id ASC LIMIT ?", (half,)
        ).fetchall()
        curr_rows = db().execute(
            "SELECT trace_id, prompt, response, status, latency_ms FROM traces ORDER BY id DESC LIMIT ?", (half,)
        ).fetchall()
        base_traces = [core.Trace(r["trace_id"], r["prompt"], r["response"], r["status"], r["latency_ms"]) for r in base_rows]
        curr_traces = [core.Trace(r["trace_id"], r["prompt"], r["response"], r["status"], r["latency_ms"]) for r in reversed(curr_rows)]
        return ml.detect_drift(base_traces, curr_traces).to_dict()

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
                "p50_ms": pct(50), "p95_ms": pct(95),
                "taxonomy": taxonomy_stats(), "drift": drift_stats()}

    def purge_old() -> None:
        days = float(os.environ.get("TRACE_RETENTION_DAYS", "30"))
        if days > 0:
            db().execute("DELETE FROM traces WHERE ts < ?", (time.time() - days * 86400,))

    def semantic_clusters_for(thr: float) -> list[ml.SemanticCluster]:
        count, newest = db().execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM traces WHERE status != 'ok'").fetchone()
        key = ("semantic", count, newest, thr, CLUSTER_LIMIT)
        if key not in cache:
            rows = db().execute(
                "SELECT trace_id, prompt, response, status, latency_ms FROM traces "
                "WHERE status != 'ok' ORDER BY id DESC LIMIT ?", (CLUSTER_LIMIT,)).fetchall()
            traces = [core.Trace(r["trace_id"], r["prompt"], r["response"], r["status"], r["latency_ms"])
                      for r in reversed(rows)]
            if len(cache) >= CACHE_SIZE:
                cache.pop(next(iter(cache)))
            cache[key] = ml.semantic_cluster(traces, thr)
        return cache[key]

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    @app.get("/metrics")
    def metrics():
        s = stats()
        drift = s.get("drift", {})
        body = (
            f"observatory_traces_total {s['total']}\n"
            f"observatory_failures_total {s['failures']}\n"
            f"observatory_failure_rate {s['failure_rate']:.4f}\n"
            f"observatory_latency_p95_ms {s['p95_ms']:.1f}\n"
            f"observatory_centroid_drift {drift.get('centroid_drift', 0.0):.4f}\n"
            f"observatory_latency_psi {drift.get('latency_psi', 0.0):.4f}\n"
        )
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

    @app.get("/api/taxonomy")
    def api_taxonomy():
        return jsonify(taxonomy_stats())

    @app.get("/api/drift")
    def api_drift():
        return jsonify(drift_stats())

    @app.get("/api/clusters")
    def api_clusters():
        return jsonify(summarize(clusters_for(threshold())))

    @app.get("/api/clusters/semantic")
    def api_clusters_semantic():
        return jsonify([c.to_dict() for c in semantic_clusters_for(threshold())])

    @app.get("/api/regression.py")
    def api_regression():
        if request.args.get("suite") == "advanced":
            code = ml.generate_regression_suite(semantic_clusters_for(threshold()))
        else:
            code = core.render_pytest(core.to_cases(clusters_for(threshold())))
        return Response(code, mimetype="text/x-python",
                        headers={"Content-Disposition": "attachment; filename=test_regressions.py"})

    @app.get("/")
    def index():
        s = stats()
        recent = db().execute("SELECT * FROM traces WHERE status != 'ok' ORDER BY id DESC LIMIT 8").fetchall()
        thr = threshold()
        sem = [c.to_dict() for c in semantic_clusters_for(thr)[:6]]
        return render_template("index.html", s=s, clusters=summarize(clusters_for(thr))[:5],
                               recent=recent, semantic_clusters=sem)

    @app.get("/clusters")
    def clusters_page():
        thr = threshold()
        sem = [c.to_dict() for c in semantic_clusters_for(thr)]
        return render_template("clusters.html", clusters=summarize(clusters_for(thr)),
                               semantic_clusters=sem)

    @app.get("/regression")
    def regression_page():
        thr = threshold()
        adv = request.args.get("suite") == "advanced"
        if adv:
            code = ml.generate_regression_suite(semantic_clusters_for(thr))
        else:
            code = core.render_pytest(core.to_cases(clusters_for(thr)))
        return render_template("regression.html", code=code, is_advanced=adv)

    return app
