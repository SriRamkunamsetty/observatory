"""Accessibility tests: semantics, labelling, CSP-friendly markup and WCAG AA colour contrast."""
import os
import re
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

from observatory.server import create_app

KEY = {"X-API-Key": "test-key"}
CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "observatory", "static", "style.css")


class Audit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found, self.headings, self.title = [], [], ""
        self._in_title, self._label_depth, self.issues = False, 0, []
        self.counts = {"table": 0, "caption": 0, "h1": 0}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.found.append((tag, a))
        if "style" in a:
            self.issues.append(f"inline style on <{tag}>")
        if tag in self.counts:
            self.counts[tag] += 1
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        if tag == "title":
            self._in_title = True
        if tag == "label":
            self._label_depth += 1
        if tag == "th" and "scope" not in a:
            self.issues.append("<th> without scope")
        if tag == "progress" and not a.get("aria-label"):
            self.issues.append("<progress> without aria-label")
        if tag == "img" and "alt" not in a:
            self.issues.append("<img> without alt")
        if tag in ("input", "textarea") and a.get("type") != "hidden":
            labelled = self._label_depth > 0 or a.get("aria-label") or a.get("id") in self.label_targets
            if not labelled:
                self.issues.append(f"unlabelled <{tag} name={a.get('name')}>")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "label":
            self._label_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def audit(self, html):
        self.label_targets = set(re.findall(r'<label[^>]*\sfor="([^"]+)"', html))
        self.feed(html)
        return self


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def read_css():
    with open(CSS, encoding="utf-8") as fh:
        return fh.read()


def theme_tokens():
    css = read_css()
    pattern = r"--([\w-]+):(#[0-9a-fA-F]{6})"
    dark = dict(re.findall(pattern, css.split("@media")[0]))
    light = {**dark, **dict(re.findall(pattern, css.split("@media (prefers-color-scheme:light)")[1]))}
    return {"dark": dark, "light": light}


class AccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"API_KEY": "test-key"})
        self.env.start()
        self.app = create_app(os.path.join(tempfile.mkdtemp(), "a.db"), {"RATE_LIMIT": 10000})
        self.pages = {}
        client = self.app.test_client()
        attrs = [{"key": "gen_ai.prompt", "value": {"stringValue": "reset my password"}},
                 {"key": "gen_ai.completion", "value": {"stringValue": "timeout error"}}]
        span = {"traceId": "t1", "startTimeUnixNano": "0", "endTimeUnixNano": "5000000", "status": {"code": 2},
                "attributes": attrs}
        client.post("/v1/traces", json={"resourceSpans": [{"scopeSpans": [{"spans": [span]}]}]}, headers=KEY)
        for path in ("/", "/clusters", "/regression"):
            self.pages[path] = client.get(path).get_data(as_text=True)

    def tearDown(self):
        self.env.stop()

    def test_every_page_meets_structural_requirements(self):
        for name, html in self.pages.items():
            page = Audit().audit(html)
            tags = [t for t, _ in page.found]
            with self.subTest(page=name):
                self.assertEqual(page.found[0][1].get("lang") if page.found[0][0] == "html" else None, "en")
                self.assertTrue(page.title.strip(), "empty <title>")
                self.assertEqual(page.counts["h1"], 1, "exactly one h1")
                self.assertIn(("main", {"id": "main", "tabindex": "-1"}), page.found)
                skip = next(a for t, a in page.found if t == "a")
                self.assertEqual((skip.get("class"), skip.get("href")), ("skip", "#main"))
                self.assertEqual(page.counts["table"], page.counts["caption"], "every table needs a caption")
                self.assertGreaterEqual(tags.count("nav"), 2)
                for prev, cur in zip(page.headings, page.headings[1:]):
                    self.assertLessEqual(cur, prev + 1, "heading levels must not skip")
                self.assertEqual(page.issues, [])

    def test_page_titles_are_unique(self):
        titles = [Audit().audit(h).title for h in self.pages.values()]
        self.assertEqual(len(titles), len(set(titles)))

    def test_colour_contrast_meets_wcag_aa_in_both_themes(self):
        pairs = [("tx", "bg"), ("tx", "card"), ("mut", "bg"), ("mut", "card"), ("mut", "head"), ("ac", "bg"),
                 ("ac", "card"), ("ac", "head"), ("btn-tx", "ac"), ("ok", "okbg"), ("bad", "badbg"), ("warn", "warnbg")]
        for theme, tok in theme_tokens().items():
            for fg, bg in pairs:
                with self.subTest(theme=theme, fg=fg, bg=bg):
                    self.assertGreaterEqual(contrast(tok[fg], tok[bg]), 4.5)

    def test_css_declares_focus_reduced_clutter_and_responsive_rules(self):
        css = read_css()
        for needle in (":focus-visible", "prefers-color-scheme:light", "@media (max-width", ".skip", ".sr{"):
            self.assertIn(needle, css)


if __name__ == "__main__":
    unittest.main()
