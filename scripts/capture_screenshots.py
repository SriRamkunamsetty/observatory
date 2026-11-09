"""Seed demo data (synthetic), run the real app, and capture screenshots into docs/screenshots/."""
import os
import random
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["API_KEY"] = "demo-key"

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from observatory.server import create_app  # noqa: E402

PAGES = [("dashboard", "/"), ("clusters", "/clusters"), ("regression-tests", "/regression")]
OK = [("what is the capital of france", "Paris."), ("summarise this article for me", "Here is a short summary."),
      ("write a haiku about rain", "Soft rain taps the glass."), ("convert 10 km to miles", "About 6.2 miles.")]
BAD = [("reset my password for user%d@corp.com", "Error: upstream timeout while resetting password", 2),
       ("cite a peer reviewed paper on %s", "According to Smith et al. 2031 (this citation does not exist)", 1),
       ("how do I cancel my order %d", "I'm sorry, I can't help with cancellations", 1)]


def span(i, prompt, completion, error, ms, flagged=False):
    attrs = [{"key": "gen_ai.prompt", "value": {"stringValue": prompt}},
             {"key": "gen_ai.completion", "value": {"stringValue": completion}}]
    if flagged:
        attrs.append({"key": "quality.status", "value": {"stringValue": "flagged"}})
    return {"traceId": f"{i:032x}", "startTimeUnixNano": "0", "endTimeUnixNano": str(int(ms * 1e6)),
            "status": {"code": 2 if error else 1}, "attributes": attrs}


def seed(app):
    rnd = random.Random(7)
    spans = []
    for i in range(160):
        if rnd.random() < 0.72:
            p, r = rnd.choice(OK)
            spans.append(span(i, p, r, False, rnd.randint(60, 700)))
        else:
            tpl, resp, kind = rnd.choices(BAD, weights=[5, 3, 3])[0]
            arg = rnd.choice(["quantum computing", "gut microbiome", "solar cells"]) if "%s" in tpl else rnd.randint(100, 999)
            prompt = tpl % arg if "%" in tpl else tpl
            spans.append(span(i, prompt, resp, kind == 2, rnd.randint(400, 2500), flagged=kind != 2))
    body = {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}
    assert app.test_client().post("/v1/traces", json=body, headers={"X-API-Key": "demo-key"}).status_code == 200


def main():
    out = os.path.join(ROOT, "docs", "screenshots")
    os.makedirs(out, exist_ok=True)
    app = create_app(os.path.join(tempfile.mkdtemp(), "demo.db"), {"RATE_LIMIT": 10000})
    seed(app)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800}, color_scheme="dark")
        for name, path in PAGES:
            page.goto(f"http://127.0.0.1:{srv.server_port}{path}")
            page.screenshot(path=os.path.join(out, f"{name}.png"), full_page=True)
        root = f"http://127.0.0.1:{srv.server_port}/"
        for name, kw in (("dashboard-light", {"viewport": {"width": 1280, "height": 800}, "color_scheme": "light"}),
                         ("dashboard-mobile", {"viewport": {"width": 390, "height": 800}, "color_scheme": "dark"})):
            extra = browser.new_page(**kw)
            extra.goto(root)
            extra.screenshot(path=os.path.join(out, f"{name}.png"), full_page=True)
        browser.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
