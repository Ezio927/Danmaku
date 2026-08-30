"""Static contract tests for the OBS page assets.

These tests exercise the public seams of the transparent OBS renderer without a
browser or a live service: the asset files must be present, the page must be
transparent/bottom-anchored/chrome-free, the client must send the exact frozen
hello frame, reconnect on the frozen bounded schedule, render the four frozen
message kinds through distinct paths, replace state on snapshot, deduplicate and
cap increments, and insert untrusted text only through DOM text APIs.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

_ASSET_DIR = Path(__file__).resolve().parent.parent / "src" / "danmaku" / "web"
_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "docs" / "protocol-fixtures"

_HELLO_FIXTURE = _FIXTURE_DIR / "client-hello.json"

_FORBIDDEN_DOM_SINKS = (
    "innerHTML",
    "insertAdjacentHTML",
    "outerHTML",
    "document.write",
    "eval(",
)

_RECONNECT_DELAYS_LITERAL = "[500, 1000, 2000, 4000, 5000]"


def _read(relative_name: str) -> str:
    path = _ASSET_DIR / relative_name
    if not path.is_file():
        raise FileNotFoundError(f"missing asset: {path}")
    return path.read_text(encoding="utf-8")


class ObsAssetsExist(unittest.TestCase):
    def test_required_assets_present_and_nonempty(self):
        for name in ("index.html", "style.css", "app.js"):
            content = _read(name)
            self.assertTrue(content.strip(), f"{name} must not be empty")


class ObsPageStructure(unittest.TestCase):
    def test_html_is_a_document_with_message_container(self):
        html = _read("index.html")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('id="messages"', html)

    def test_html_references_packaged_assets(self):
        html = _read("index.html")
        self.assertIn('href="/assets/style.css"', html)
        self.assertIn('src="/assets/app.js"', html)

    def test_body_has_no_visible_chrome_markup(self):
        html = _read("index.html")
        self.assertNotIn("<header", html.lower())
        self.assertNotIn("<footer", html.lower())
        self.assertNotIn("<nav", html.lower())


class ObsPageVisualContract(unittest.TestCase):
    def test_css_is_transparent(self):
        css = _read("style.css")
        self.assertIn("background: transparent", css)

    def test_css_hides_scrollbar(self):
        css = _read("style.css")
        self.assertIn("overflow: hidden", css)

    def test_css_bottom_anchors_messages(self):
        css = _read("style.css")
        self.assertIn("bottom: 0", css)
        self.assertIn("justify-content: flex-end", css)

    def test_css_wraps_text_without_scaling(self):
        css = _read("style.css")
        self.assertTrue(
            "overflow-wrap" in css or "word-break" in css or "flex-wrap" in css,
            "narrow width must wrap text instead of scaling",
        )


class ObsClientHelloContract(unittest.TestCase):
    def test_sends_exact_frozen_hello_frame(self):
        app = _read("app.js")
        expected = json.dumps(
            json.loads(_HELLO_FIXTURE.read_text(encoding="utf-8")),
            separators=(",", ":"),
        )
        self.assertIn(expected, app)


class ObsReconnectScheduleContract(unittest.TestCase):
    def test_reconnects_on_frozen_bounded_schedule(self):
        app = _read("app.js")
        self.assertIn(_RECONNECT_DELAYS_LITERAL, app)


class ObsRenderPathsContract(unittest.TestCase):
    def test_all_four_kinds_have_distinct_render_paths(self):
        app = _read("app.js")
        for kind in ("danmaku", "gift", "guard", "superChat"):
            self.assertIn(f'"{kind}"', app)
        self.assertIn("switch", app)

    def test_snapshot_replaces_and_increment_appends(self):
        app = _read("app.js")
        self.assertIn('"snapshot"', app)
        self.assertIn('"message.created"', app)
        self.assertIn("knownIds.clear", app)
        self.assertIn("appendChild", app)

    def test_increment_deduplicates_and_caps_at_100(self):
        app = _read("app.js")
        self.assertIn("knownIds.has", app)
        self.assertIn("100", app)
        self.assertIn("removeChild", app)


class ObsSafeTextRenderingContract(unittest.TestCase):
    def test_untrusted_text_uses_text_api_only(self):
        app = _read("app.js")
        self.assertIn("textContent", app)

    def test_never_uses_html_sinks(self):
        app = _read("app.js")
        for sink in _FORBIDDEN_DOM_SINKS:
            self.assertNotIn(sink, app, f"forbidden DOM sink present: {sink}")


if __name__ == "__main__":
    unittest.main()
