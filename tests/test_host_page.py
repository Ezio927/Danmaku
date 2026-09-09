"""Focused tests for the compact Host monitoring page and its service surface.

The Host page (``/host``) and its WebSocket (``/ws/host``) are the dedicated,
unfiltered monitoring surface layered on the same loopback service as OBS. These
tests cover the static page contract (safe text rendering, hello frame, reconnect
schedule, snapshot/increment semantics, 1000-message cap, and the bounded
timeline controls: paused-follow unread counting, the keyboard-accessible
return-to-latest prompt, and the view-only clear) and the service composition
(route serving, unfiltered delivery, and reconnect replacement) while leaving the
frozen OBS protocol v1 surface untouched.
"""

from __future__ import annotations

import json
import re
import socket
import sys
import tomllib
import unittest
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from danmaku.core.hub import DistributionHub  # noqa: E402
from danmaku.core.model import Message  # noqa: E402
from danmaku.server.app import create_app  # noqa: E402
from danmaku.server.config import ServiceConfig  # noqa: E402
from danmaku.server.filtering import FilteringPolicy  # noqa: E402
from danmaku.server.runner import Service  # noqa: E402

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

HELLO = '{"protocolVersion":1,"type":"hello","payload":{}}'


def _read(relative_name: str) -> str:
    path = _ASSET_DIR / relative_name
    if not path.is_file():
        raise FileNotFoundError(f"missing asset: {path}")
    return path.read_text(encoding="utf-8")


def make_message(
    sequence: int,
    kind: str = "danmaku",
    user_id: str = "user:alice",
    name: str = "Alice",
) -> Message:
    data = {
        "danmaku": {"text": "hello"},
        "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
        "guard": {"tier": "captain", "months": 1},
        "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
    }[kind]
    return Message.from_dict(
        {
            "id": f"hp:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": user_id, "name": name},
            "data": data,
        }
    )


def _fixed_clock() -> int:
    """Deterministic clock that keeps every fixed 2026-01-01 fixture in-window."""
    return 0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class HostAssetsExist(unittest.TestCase):
    def test_required_assets_present_and_nonempty(self):
        for name in ("host.html", "host.css", "host.js"):
            content = _read(name)
            self.assertTrue(content.strip(), f"{name} must not be empty")


class HostPageStructure(unittest.TestCase):
    def test_html_is_a_document_with_timeline_container(self):
        html = _read("host.html")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('id="timeline"', html)

    def test_html_references_packaged_host_assets(self):
        html = _read("host.html")
        self.assertIn('href="/assets/host.css"', html)
        self.assertIn('src="/assets/host.js"', html)


class HostClientContract(unittest.TestCase):
    def test_connects_to_host_websocket(self):
        app = _read("host.js")
        self.assertIn("/ws/host", app)
        self.assertIn("ws://127.0.0.1", app)

    def test_sends_exact_frozen_hello_frame(self):
        app = _read("host.js")
        expected = json.dumps(
            json.loads(_HELLO_FIXTURE.read_text(encoding="utf-8")),
            separators=(",", ":"),
        )
        self.assertIn(expected, app)

    def test_reconnects_on_frozen_bounded_schedule(self):
        app = _read("host.js")
        self.assertIn(_RECONNECT_DELAYS_LITERAL, app)


class HostRenderContract(unittest.TestCase):
    def test_all_four_kinds_have_distinct_render_paths(self):
        app = _read("host.js")
        for kind in ("danmaku", "gift", "guard", "superChat"):
            self.assertIn(f'"{kind}"', app)
        self.assertIn("switch", app)

    def test_snapshot_replaces_and_increment_appends(self):
        app = _read("host.js")
        self.assertIn('"snapshot"', app)
        self.assertIn('"message.created"', app)
        self.assertIn("knownIds.clear", app)
        self.assertIn("appendChild", app)

    def test_increment_deduplicates_and_caps_at_1000(self):
        app = _read("host.js")
        self.assertIn("knownIds.has", app)
        self.assertIn("1000", app)
        self.assertIn("removeChild", app)


class HostSafeTextRenderingContract(unittest.TestCase):
    def test_untrusted_text_uses_text_api_only(self):
        app = _read("host.js")
        self.assertIn("textContent", app)

    def test_never_uses_html_sinks(self):
        app = _read("host.js")
        for sink in _FORBIDDEN_DOM_SINKS:
            self.assertNotIn(sink, app, f"forbidden DOM sink present: {sink}")


class HostTimelineControlsContract(unittest.TestCase):
    def test_html_declares_clear_and_new_messages_controls(self):
        html = _read("host.html")
        self.assertIn('id="clear"', html)
        self.assertIn('id="new-messages"', html)

    def test_controls_are_keyboard_accessible_buttons(self):
        html = _read("host.html")
        self.assertRegex(html, r'<button[^>]*id="clear"')
        self.assertRegex(html, r'<button[^>]*id="new-messages"')

    def test_scroll_away_pauses_follow_and_counts_unread(self):
        # Messages are still appended while paused (appendChild is unconditional),
        # so none are lost; they are counted in ``unread``.
        app = _read("host.js")
        self.assertIn("isNearBottom", app)
        self.assertIn("unread += 1", app)
        self.assertIn("paused", app)
        self.assertIn("appendChild", app)

    def test_new_message_prompt_returns_to_latest_and_resets_unread(self):
        app = _read("host.js")
        self.assertIn('newMessagesButton.addEventListener("click", returnToLatest)', app)
        self.assertIn("scrollToBottom", app)
        self.assertIn("unread = 0", app)

    def test_snapshot_resets_view_local_controls_deterministically(self):
        app = _read("host.js")
        self.assertIn("resetFollowState", app)
        self.assertRegex(
            app,
            r"function applySnapshot\(messages\)\s*\{[^}]*resetFollowState\(\)",
        )

    def test_clear_is_view_only_and_preserves_connection_state(self):
        app = _read("host.js")
        match = re.search(r"function clearView\(\)\s*\{[^}]*\}", app)
        self.assertIsNotNone(match, "clearView must be defined")
        body = match.group(0)
        self.assertIn("clearList", body)
        for token in ("socket", "connect(", "reconnectAttempt", "scheduleReconnect", "WebSocket", "send("):
            self.assertNotIn(
                token, body, f"clearView must not touch connection state: {token}"
            )

    def test_clear_control_sends_no_protocol_frame(self):
        # The Host client sends exactly one frame (the frozen hello); the clear
        # control is a pure client-side DOM operation and never mutates canonical
        # state, OBS delivery, or filtering over the wire.
        app = _read("host.js")
        self.assertEqual(app.count("socket.send("), 1)
        self.assertIn("socket.send(HELLO_FRAME)", app)


class HostTopPresentationStructure(unittest.TestCase):
    def test_html_declares_top_card_and_skip_control(self):
        html = _read("host.html")
        self.assertIn('id="top-card"', html)
        self.assertIn('id="skip"', html)

    def test_skip_control_is_keyboard_accessible_button(self):
        html = _read("host.html")
        self.assertRegex(html, r'<button[^>]*id="skip"')

    def test_top_card_declares_text_node_fields(self):
        html = _read("host.html")
        for field in (
            "top-tier",
            "top-amount",
            "top-user",
            "top-text",
            "top-pending",
            "top-remaining",
        ):
            self.assertIn(f'id="{field}"', html)


class HostTopPresentationContract(unittest.TestCase):
    def test_presentation_interval_is_three_seconds(self):
        app = _read("host.js")
        self.assertIn("PRESENTATION_INTERVAL_MS = 3000", app)

    def test_received_super_chat_enters_fifo_pending_queue(self):
        app = _read("host.js")
        self.assertIn("pendingQueue", app)
        self.assertIn("pendingQueue.push(message)", app)

    def test_earliest_pending_becomes_single_active_card(self):
        app = _read("host.js")
        self.assertIn("pendingQueue.shift()", app)
        self.assertIn("activeCard = { message: front, displayedAtMs: dueMs }", app)

    def test_expiry_uses_duration_seconds(self):
        app = _read("host.js")
        self.assertIn("activeCard.message.data.durationSeconds * 1000", app)

    def test_top_card_derives_deterministic_tier_and_color(self):
        app = _read("host.js")
        self.assertIn("SUPER_CHAT_TIERS", app)
        self.assertIn("function superChatTier(", app)
        self.assertIn('topCard.className = "top-card top-card--" + tier.css', app)
        css = _read("host.css")
        for color in ("blue", "cyan", "indigo", "purple", "pink", "red", "gold"):
            self.assertIn(f"top-card--{color}", css)

    def test_top_card_renders_fields_with_text_api_only(self):
        app = _read("host.js")
        for assignment in (
            "topTier.textContent = tier.label",
            "topAmount.textContent = formatMoney(message.data.amountMilliCny)",
            "topUser.textContent = message.user.name",
            "topText.textContent = message.data.text",
            'topPending.textContent = pendingQueue.length + " pending"',
            'topRemaining.textContent = formatDuration(seconds) + " remaining"',
        ):
            self.assertIn(assignment, app)

    def test_snapshot_replaces_presentation_state_without_replay_animation(self):
        app = _read("host.js")
        self.assertRegex(
            app,
            r"function applySnapshot\(messages\)\s*\{[^}]*resetPresentation\(\)",
        )
        self.assertIn("renderTopCard(false)", app)

    def test_skip_control_advances_presentation_view_locally(self):
        app = _read("host.js")
        self.assertIn('skipButton.addEventListener("click", skipPresentation)', app)
        self.assertIn("function skipPresentation()", app)

    def test_skip_sends_no_protocol_frame(self):
        # Skip mutates only view-local presentation state; the Host client still
        # sends exactly one frame (the frozen hello) over the wire.
        app = _read("host.js")
        self.assertEqual(app.count("socket.send("), 1)


class HostConnectionStatusStructure(unittest.TestCase):
    def test_html_declares_status_region_and_indicators(self):
        html = _read("host.html")
        self.assertIn('id="status"', html)
        self.assertIn('id="service-status"', html)
        self.assertIn('id="feed-status"', html)
        self.assertIn('id="service-status-label"', html)
        self.assertIn('id="feed-status-label"', html)

    def test_status_region_is_an_accessible_live_region(self):
        html = _read("host.html")
        self.assertIn('role="status"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('aria-atomic="true"', html)

    def test_status_dots_are_decorative(self):
        html = _read("host.html")
        self.assertIn('class="status__dot" aria-hidden="true"', html)


class HostConnectionStatusContract(unittest.TestCase):
    def test_probes_only_the_existing_loopback_health_route(self):
        app = _read("host.js")
        self.assertIn('"/health"', app)
        self.assertIn("http://127.0.0.1", app)
        self.assertIn("fetch(healthUrl())", app)

    def test_health_probe_adds_no_query_parameters(self):
        app = _read("host.js")
        self.assertNotIn("/health?", app)

    def test_derives_all_four_deterministic_states(self):
        app = _read("host.js")
        for state in ("connecting", "connected", "reconnecting", "unavailable"):
            self.assertIn(state, app)

    def test_local_service_indicator_covers_available_and_unavailable(self):
        app = _read("host.js")
        self.assertIn("Local service: available", app)
        self.assertIn("Local service: unavailable", app)

    def test_host_feed_indicator_covers_all_four_states(self):
        app = _read("host.js")
        for text in (
            "Host feed: connecting",
            "Host feed: connected",
            "Host feed: reconnecting",
            "Host feed: unavailable",
        ):
            self.assertIn(text, app)

    def test_feed_state_is_derived_from_websocket_ready_state(self):
        app = _read("host.js")
        self.assertIn("WebSocket.OPEN", app)
        self.assertIn("WebSocket.CONNECTING", app)

    def test_unavailable_is_derived_from_health_probe_failure(self):
        app = _read("host.js")
        self.assertIn("if (serviceUp === false)", app)

    def test_status_labels_rendered_with_text_api_only(self):
        app = _read("host.js")
        self.assertIn("serviceStatusLabel.textContent", app)
        self.assertIn("feedStatusLabel.textContent", app)
        for sink in _FORBIDDEN_DOM_SINKS:
            self.assertNotIn(sink, app, f"forbidden DOM sink present: {sink}")

    def test_status_labels_never_interpolate_error_details(self):
        app = _read("host.js")
        for leak in (
            "event.reason",
            "event.code",
            "error.message",
            "e.message",
            "err.message",
            ".stack",
        ):
            self.assertNotIn(leak, app, f"error detail leaked: {leak}")

    def test_health_probe_adds_no_protocol_frame(self):
        app = _read("host.js")
        self.assertEqual(app.count("socket.send("), 1)

    def test_reconnect_schedule_remains_bounded(self):
        app = _read("host.js")
        self.assertIn(_RECONNECT_DELAYS_LITERAL, app)

    def test_status_dot_colors_declared_for_each_state(self):
        css = _read("host.css")
        for state in ("available", "connected", "connecting", "reconnecting", "unavailable"):
            self.assertIn(f"status__item--{state}", css)


class HostPackagingTests(unittest.TestCase):
    def test_host_assets_declared_as_package_data(self):
        pyproject = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        declared = pyproject["tool"]["setuptools"]["package-data"]["danmaku"]
        for name in ("host.html", "host.js", "host.css"):
            self.assertIn(f"web/{name}", declared)


class _AppMixin:
    async def asyncSetUp(self):
        self.hub = DistributionHub(clock=_fixed_clock)
        self.app = create_app(hub=self.hub, asset_root=_ASSET_DIR)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()


class HostHttpTests(_AppMixin, unittest.IsolatedAsyncioTestCase):
    async def test_host_page_served_as_html(self):
        resp = await self.client.get("/host")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/html")
        body = await resp.text()
        self.assertIn("<!DOCTYPE html>", body)
        self.assertIn('id="timeline"', body)

    async def test_host_assets_serve_correct_content_types(self):
        resp = await self.client.get("/assets/host.js")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/javascript")
        resp = await self.client.get("/assets/host.css")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/css")

    async def test_unknown_and_traversal_assets_404(self):
        resp = await self.client.get("/assets/secret.txt")
        self.assertEqual(resp.status, 404)
        resp = await self.client.get("/assets/../etc/passwd")
        self.assertEqual(resp.status, 404)

    async def test_head_405_on_host_routes(self):
        for path in ("/host", "/ws/host"):
            with self.subTest(path=path):
                resp = await self.client.head(path)
                self.assertEqual(resp.status, 405)

    async def test_non_get_405_on_host_routes(self):
        for path in ("/host", "/ws/host"):
            for method in ("post", "put", "delete", "patch"):
                with self.subTest(path=path, method=method):
                    resp = await getattr(self.client, method)(path)
                    self.assertEqual(resp.status, 405)

    async def test_host_ws_without_upgrade_400(self):
        resp = await self.client.get("/ws/host")
        self.assertEqual(resp.status, 400)

    async def test_host_ws_query_param_400(self):
        resp = await self.client.get("/ws/host?x=1")
        self.assertEqual(resp.status, 400)


class HostWebSocketTests(_AppMixin, unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_first_then_ordered_increments(self):
        self.hub.publish(make_message(1))
        self.hub.publish(make_message(2))
        ws = await self.client.ws_connect("/ws/host")

        first = json.loads((await ws.receive()).data)
        self.assertEqual(first["type"], "snapshot")
        self.assertEqual(
            [m["sequence"] for m in first["payload"]["messages"]], [1, 2]
        )

        await ws.send_str(HELLO)
        self.hub.publish(make_message(3))
        self.hub.publish(make_message(4))
        for expected in (3, 4):
            frame = json.loads((await ws.receive()).data)
            self.assertEqual(frame["type"], "message.created")
            self.assertEqual(frame["payload"]["message"]["sequence"], expected)
        await ws.close()

    async def test_host_ws_is_unfiltered_while_obs_ws_is_filtered(self):
        policy = FilteringPolicy(deny_user_ids={"user:alice"})
        hub = DistributionHub(filter=policy.is_suppressed, clock=_fixed_clock)
        app = create_app(hub=hub, asset_root=_ASSET_DIR)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            hub.publish(make_message(50, user_id="user:alice"))
            hub.publish(make_message(51, user_id="user:bob", name="Bob"))

            host_ws = await client.ws_connect("/ws/host")
            host_snapshot = json.loads((await host_ws.receive()).data)
            host_seqs = [m["sequence"] for m in host_snapshot["payload"]["messages"]]
            self.assertIn(50, host_seqs)
            self.assertIn(51, host_seqs)
            await host_ws.send_str(HELLO)

            obs_ws = await client.ws_connect("/ws")
            obs_snapshot = json.loads((await obs_ws.receive()).data)
            obs_seqs = [m["sequence"] for m in obs_snapshot["payload"]["messages"]]
            self.assertNotIn(50, obs_seqs)
            self.assertIn(51, obs_seqs)
            await obs_ws.send_str(HELLO)

            await host_ws.close()
            await obs_ws.close()
        finally:
            await client.close()

    async def test_reconnect_receives_replacement_snapshot(self):
        ws = await self.client.ws_connect("/ws/host")
        await ws.receive()  # empty snapshot
        await ws.send_str(HELLO)

        self.hub.publish(make_message(1))
        self.hub.publish(make_message(2))
        for expected in (1, 2):
            frame = json.loads((await ws.receive()).data)
            self.assertEqual(frame["payload"]["message"]["sequence"], expected)
        await ws.close()

        self.hub.publish(make_message(3))
        self.hub.publish(make_message(4))

        reconnected = await self.client.ws_connect("/ws/host")
        snapshot = json.loads((await reconnected.receive()).data)
        self.assertEqual(snapshot["type"], "snapshot")
        self.assertEqual(
            [m["sequence"] for m in snapshot["payload"]["messages"]],
            [1, 2, 3, 4],
        )
        await reconnected.send_str(HELLO)
        self.hub.publish(make_message(5))
        frame = json.loads((await reconnected.receive()).data)
        self.assertEqual(frame["payload"]["message"]["sequence"], 5)
        await reconnected.close()


class HostServiceLoopbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_page_served_over_real_loopback_service(self):
        config = ServiceConfig(port=_free_port(), cadence_milliseconds=60000)
        service = Service(config)
        await service.start()
        try:
            url = f"http://127.0.0.1:{service.port}/host"
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.content_type, "text/html")
                    body = await resp.text()
                    self.assertIn('id="timeline"', body)
        finally:
            await service.stop()


if __name__ == "__main__":
    unittest.main()
