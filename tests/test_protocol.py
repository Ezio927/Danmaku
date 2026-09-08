"""Protocol-level tests: exact frames, HTTP routes, and WebSocket semantics."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aiohttp import WSMsgType  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from danmaku.core.hub import DistributionHub  # noqa: E402
from danmaku.core.model import Message  # noqa: E402
from danmaku.server.app import create_app  # noqa: E402
from danmaku.server.frames import (  # noqa: E402
    classify_client_frame,
    error_frame,
    message_created_frame,
    snapshot_frame,
)

FIXTURES = ROOT / "docs" / "protocol-fixtures"

HELLO = '{"protocolVersion":1,"type":"hello","payload":{}}'


def _fixed_clock() -> int:
    """Deterministic clock that keeps every fixed 2026-01-01 fixture in-window.

    Returning the epoch places the snapshot-retention cutoff at epoch minus
    five minutes, so no fixture timestamp is ever trimmed. Tests that exercise
    the retention window itself inject their own clock instead.
    """
    return 0


def _load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_message(sequence, kind="danmaku"):
    data = {
        "danmaku": {"text": "hello"},
        "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
        "guard": {"tier": "captain", "months": 1},
        "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
    }[kind]
    return Message.from_dict(
        {
            "id": f"test:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": "user:alice", "name": "Alice"},
            "data": data,
        }
    )


class FrameSerializationTests(unittest.TestCase):
    def test_snapshot_frame_matches_fixture(self):
        messages = [
            Message.from_dict(message)
            for message in _load_fixture("snapshot.json")["payload"]["messages"]
        ]
        self.assertEqual(
            json.loads(snapshot_frame(messages)), _load_fixture("snapshot.json")
        )

    def test_message_created_frame_matches_fixture_exactly(self):
        for name in (
            "message-created-danmaku.json",
            "message-created-gift.json",
            "message-created-guard.json",
            "message-created-super-chat.json",
        ):
            with self.subTest(fixture=name):
                fixture = _load_fixture(name)
                message = Message.from_dict(fixture["payload"]["message"])
                self.assertEqual(
                    message_created_frame(message),
                    (FIXTURES / name).read_text(encoding="utf-8").strip(),
                )

    def test_error_frame_matches_fixture(self):
        self.assertEqual(
            json.loads(error_frame("UNSUPPORTED_VERSION")),
            _load_fixture("error-unsupported-version.json"),
        )

    def test_snapshot_frame_orders_messages_oldest_first(self):
        messages = [make_message(i) for i in (1, 2, 3)]
        frame = json.loads(snapshot_frame(messages))
        self.assertEqual(
            [m["sequence"] for m in frame["payload"]["messages"]], [1, 2, 3]
        )


class ClassifyClientFrameTests(unittest.TestCase):
    def test_valid_hello_is_accepted(self):
        self.assertIsNone(classify_client_frame(HELLO))

    def test_fixture_hello_is_accepted(self):
        self.assertIsNone(
            classify_client_frame(
                (FIXTURES / "client-hello.json").read_text(encoding="utf-8")
            )
        )

    def test_invalid_json(self):
        self.assertEqual(classify_client_frame("{not json"), "INVALID_JSON")

    def test_non_object_json(self):
        self.assertEqual(classify_client_frame("[]"), "INVALID_FRAME")
        self.assertEqual(classify_client_frame("123"), "INVALID_FRAME")

    def test_unknown_key(self):
        self.assertEqual(
            classify_client_frame(
                '{"protocolVersion":1,"type":"hello","payload":{},"extra":1}'
            ),
            "INVALID_FRAME",
        )

    def test_missing_key(self):
        self.assertEqual(
            classify_client_frame('{"protocolVersion":1,"type":"hello"}'),
            "INVALID_FRAME",
        )

    def test_unsupported_version(self):
        self.assertEqual(
            classify_client_frame('{"protocolVersion":2,"type":"hello","payload":{}}'),
            "UNSUPPORTED_VERSION",
        )

    def test_version_string_is_invalid_frame(self):
        self.assertEqual(
            classify_client_frame('{"protocolVersion":"1","type":"hello","payload":{}}'),
            "INVALID_FRAME",
        )

    def test_unsupported_type(self):
        self.assertEqual(
            classify_client_frame('{"protocolVersion":1,"type":"bye","payload":{}}'),
            "UNSUPPORTED_TYPE",
        )

    def test_non_string_type(self):
        self.assertEqual(
            classify_client_frame('{"protocolVersion":1,"type":1,"payload":{}}'),
            "INVALID_FRAME",
        )

    def test_non_empty_payload(self):
        self.assertEqual(
            classify_client_frame('{"protocolVersion":1,"type":"hello","payload":{"x":1}}'),
            "INVALID_FRAME",
        )

    def test_oversized_text_frame(self):
        self.assertEqual(
            classify_client_frame("x" * 65537), "INVALID_FRAME"
        )

    def test_exactly_cap_is_not_oversized(self):
        self.assertEqual(classify_client_frame("x" * 65536), "INVALID_JSON")


class _AppMixin:
    async def asyncSetUp(self):
        self.hub = DistributionHub(clock=_fixed_clock)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "index.html").write_text("<html>obs</html>", encoding="utf-8")
        (root / "app.js").write_text("// js", encoding="utf-8")
        (root / "style.css").write_text("/* css */", encoding="utf-8")
        self.app = create_app(hub=self.hub, asset_root=root)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def _drain(self, ws):
        frames = []
        close_code = None
        while True:
            msg = await ws.receive()
            if msg.type == WSMsgType.CLOSE:
                close_code = msg.data
            elif msg.type == WSMsgType.CLOSED:
                if close_code is None:
                    close_code = ws.close_code
                return frames, close_code
            elif msg.type == WSMsgType.ERROR:
                return frames, close_code
            else:
                frames.append(msg)


class ProtocolHttpTests(_AppMixin, unittest.IsolatedAsyncioTestCase):
    async def test_health_exact_body(self):
        resp = await self.client.get("/health")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "application/json")
        self.assertEqual(await resp.read(), b'{"protocolVersion":1,"status":"ok"}')
        self.assertEqual(await resp.json(), {"protocolVersion": 1, "status": "ok"})

    async def test_health_wrong_method_405(self):
        for method in ("post", "put", "delete", "patch"):
            with self.subTest(method=method):
                resp = await getattr(self.client, method)("/health")
                self.assertEqual(resp.status, 405)

    async def test_head_405_on_every_frozen_get_route(self):
        for path in ("/health", "/obs", "/assets/app.js", "/ws"):
            with self.subTest(path=path):
                resp = await self.client.head(path)
                self.assertEqual(resp.status, 405)

    async def test_every_non_get_method_405_on_every_frozen_get_route(self):
        for path in ("/health", "/obs", "/assets/app.js", "/ws"):
            for method in ("post", "put", "delete", "patch", "options"):
                with self.subTest(path=path, method=method):
                    resp = await getattr(self.client, method)(path)
                    self.assertEqual(resp.status, 405)

    async def test_unknown_route_404(self):
        resp = await self.client.get("/nope")
        self.assertEqual(resp.status, 404)

    async def test_obs_returns_html(self):
        resp = await self.client.get("/obs")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/html")
        self.assertEqual(await resp.text(), "<html>obs</html>")

    async def test_obs_wrong_method_405(self):
        resp = await self.client.post("/obs")
        self.assertEqual(resp.status, 405)

    async def test_known_asset_served(self):
        resp = await self.client.get("/assets/app.js")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/javascript")
        self.assertEqual(await resp.text(), "// js")

    async def test_unknown_asset_404(self):
        resp = await self.client.get("/assets/secret.txt")
        self.assertEqual(resp.status, 404)

    async def test_traversal_asset_404(self):
        resp = await self.client.get("/assets/../etc/passwd")
        self.assertEqual(resp.status, 404)

    async def test_asset_wrong_method_405(self):
        resp = await self.client.post("/assets/app.js")
        self.assertEqual(resp.status, 405)

    async def test_ws_without_upgrade_400(self):
        resp = await self.client.get("/ws")
        self.assertEqual(resp.status, 400)

    async def test_ws_query_param_400(self):
        resp = await self.client.get("/ws?x=1")
        self.assertEqual(resp.status, 400)


class ProtocolWebSocketTests(_AppMixin, unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_first_then_ordered_increments(self):
        self.hub.publish(make_message(1))
        self.hub.publish(make_message(2))
        ws = await self.client.ws_connect("/ws")

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

    async def test_hello_receives_no_reply(self):
        ws = await self.client.ws_connect("/ws")
        await ws.receive()  # snapshot
        await ws.send_str(HELLO)
        self.hub.publish(make_message(1))
        frame = json.loads((await ws.receive()).data)
        self.assertEqual(frame["type"], "message.created")
        self.assertEqual(frame["payload"]["message"]["sequence"], 1)
        await ws.close()

    async def test_invalid_json_gets_error_and_1008(self):
        ws = await self.client.ws_connect("/ws")
        await ws.receive()  # snapshot
        await ws.send_str("{not json")
        frames, close_code = await self._drain(ws)
        self.assertEqual(len(frames), 1)
        self.assertEqual(json.loads(frames[0].data)["payload"]["code"], "INVALID_JSON")
        self.assertEqual(close_code, 1008)

    async def test_binary_frame_gets_invalid_frame_and_1008(self):
        ws = await self.client.ws_connect("/ws")
        await ws.receive()  # snapshot
        await ws.send_bytes(b"\x00\x01")
        frames, close_code = await self._drain(ws)
        self.assertEqual(len(frames), 1)
        self.assertEqual(json.loads(frames[0].data)["payload"]["code"], "INVALID_FRAME")
        self.assertEqual(close_code, 1008)

    async def test_oversized_text_frame_gets_invalid_frame_and_1008(self):
        ws = await self.client.ws_connect("/ws")
        await ws.receive()  # snapshot
        await ws.send_str("x" * 65537)
        frames, close_code = await self._drain(ws)
        self.assertEqual(len(frames), 1)
        self.assertEqual(json.loads(frames[0].data)["payload"]["code"], "INVALID_FRAME")
        self.assertEqual(close_code, 1008)

    async def test_unsupported_version_gets_error_and_1008(self):
        ws = await self.client.ws_connect("/ws")
        await ws.receive()  # snapshot
        await ws.send_str('{"protocolVersion":2,"type":"hello","payload":{}}')
        frames, close_code = await self._drain(ws)
        self.assertEqual(len(frames), 1)
        self.assertEqual(
            json.loads(frames[0].data)["payload"]["code"], "UNSUPPORTED_VERSION"
        )
        self.assertEqual(close_code, 1008)

    async def test_unsupported_type_gets_error_and_1008(self):
        ws = await self.client.ws_connect("/ws")
        await ws.receive()  # snapshot
        await ws.send_str('{"protocolVersion":1,"type":"bye","payload":{}}')
        frames, close_code = await self._drain(ws)
        self.assertEqual(len(frames), 1)
        self.assertEqual(
            json.loads(frames[0].data)["payload"]["code"], "UNSUPPORTED_TYPE"
        )
        self.assertEqual(close_code, 1008)

    async def test_second_hello_is_invalid_frame(self):
        ws = await self.client.ws_connect("/ws")
        await ws.receive()  # snapshot
        await ws.send_str(HELLO)
        await ws.send_str(HELLO)
        frames, close_code = await self._drain(ws)
        self.assertEqual(len(frames), 1)
        self.assertEqual(json.loads(frames[0].data)["payload"]["code"], "INVALID_FRAME")
        self.assertEqual(close_code, 1008)

    async def test_missing_hello_closes_1008_without_error(self):
        app = create_app(
            hub=self.hub, asset_root=Path(self.tmp.name), hello_timeout=0.05
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            await ws.receive()  # snapshot
            frames, close_code = await self._drain(ws)
            self.assertEqual(frames, [])
            self.assertEqual(close_code, 1008)
        finally:
            await client.close()

    async def test_multiple_clients_get_independent_streams(self):
        first = await self.client.ws_connect("/ws")
        second = await self.client.ws_connect("/ws")

        await first.receive()  # snapshot
        await second.receive()  # snapshot

        await first.send_str(HELLO)
        await second.send_str(HELLO)

        self.hub.publish(make_message(1))
        self.hub.publish(make_message(2))

        for ws in (first, second):
            for expected in (1, 2):
                frame = json.loads((await ws.receive()).data)
                self.assertEqual(frame["type"], "message.created")
                self.assertEqual(frame["payload"]["message"]["sequence"], expected)

        await first.close()
        await second.close()

    async def test_reconnect_receives_replacement_snapshot(self):
        ws = await self.client.ws_connect("/ws")
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

        reconnected = await self.client.ws_connect("/ws")
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

    async def test_no_message_gap_between_snapshot_and_increments(self):
        ws = await self.client.ws_connect("/ws")
        for sequence in range(1, 6):
            self.hub.publish(make_message(sequence))

        snapshot = json.loads((await ws.receive()).data)
        snapshot_seqs = [
            m["sequence"] for m in snapshot["payload"]["messages"]
        ]
        await ws.send_str(HELLO)

        increment_seqs = []
        for _ in range(5 - len(snapshot_seqs)):
            frame = json.loads((await ws.receive()).data)
            increment_seqs.append(frame["payload"]["message"]["sequence"])

        self.assertEqual(sorted(snapshot_seqs + increment_seqs), [1, 2, 3, 4, 5])
        await ws.close()

    async def test_slow_client_closed_with_1013(self):
        hub = DistributionHub(capacity=2)
        app = create_app(hub=hub, asset_root=Path(self.tmp.name))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            await ws.receive()  # empty snapshot
            # A full queue of paid interactions has no evictable danmaku, so
            # the slow client fails closed with 1013 without dropping a paid
            # interaction.
            hub.publish(make_message(1, "guard"))
            hub.publish(make_message(2, "superChat"))
            hub.publish(make_message(3, "guard"))
            frames, close_code = await self._drain(ws)
            self.assertEqual(
                [json.loads(f.data)["payload"]["message"]["sequence"] for f in frames],
                [1, 2],
            )
            self.assertEqual(close_code, 1013)
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
