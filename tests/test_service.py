"""Service-level tests: config validation, loopback bind, lifecycle, and WS."""

import asyncio
import dataclasses
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.model import Message  # noqa: E402
from danmaku.server.config import ConfigError, ServiceConfig  # noqa: E402
from danmaku.server.filtering import FilteringPolicy  # noqa: E402
from danmaku.server.runner import HOST_TIMELINE_MAX_MESSAGES, Service  # noqa: E402

HELLO = '{"protocolVersion":1,"type":"hello","payload":{}}'


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_message(sequence, kind="danmaku"):
    data = {
        "danmaku": {"text": "hello"},
        "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
        "guard": {"tier": "captain", "months": 1},
        "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
    }[kind]
    return Message.from_dict(
        {
            "id": f"svc:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": "user:alice", "name": "Alice"},
            "data": data,
        }
    )


def make_gift(sequence, quantity=1, amount_milli_cny=1000):
    return Message.from_dict(
        {
            "id": f"svc-gift:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": "gift",
            "user": {"id": "user:bob", "name": "Bob"},
            "data": {
                "giftName": "Star",
                "quantity": quantity,
                "totalAmountMilliCny": amount_milli_cny,
            },
        }
    )


def make_message_at(sequence, received_at):
    return Message.from_dict(
        {
            "id": f"svc:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": received_at,
            "source": "mock",
            "kind": "danmaku",
            "user": {"id": "user:alice", "name": "Alice"},
            "data": {"text": "hello"},
        }
    )


def _fixed_clock() -> int:
    """Deterministic clock that keeps every fixed 2026-01-01 fixture in-window.

    Returning the epoch places the snapshot-retention cutoff at epoch minus
    five minutes, so no fixture timestamp is ever trimmed. Tests that exercise
    the retention window itself inject their own clock instead.
    """
    return 0


class ConfigTests(unittest.TestCase):
    def _valid(self):
        return {
            "configVersion": 1,
            "service": {"host": "127.0.0.1", "port": 17391},
            "mock": {"cadenceMilliseconds": 1000},
            "snapshot": {"maxMessages": 100},
            "obs": {
                "denyUserIds": [],
                "denyNicknames": [],
                "keywords": [],
                "giftThresholdMilliCny": 100,
            },
        }

    def test_defaults(self):
        config = ServiceConfig.default()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 17391)
        self.assertEqual(config.cadence_milliseconds, 1000)
        self.assertEqual(config.max_messages, 100)

    def test_valid_dict(self):
        config = ServiceConfig.from_dict(self._valid())
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 17391)

    def test_unknown_top_level_key_rejected(self):
        value = self._valid()
        value["secret"] = "x"
        with self.assertRaises(ConfigError):
            ServiceConfig.from_dict(value)

    def test_unknown_service_key_rejected(self):
        value = self._valid()
        value["service"]["host"] = "127.0.0.1"
        value["service"]["interface"] = "eth0"
        with self.assertRaises(ConfigError):
            ServiceConfig.from_dict(value)

    def test_config_version_must_be_one(self):
        value = self._valid()
        value["configVersion"] = 2
        with self.assertRaises(ConfigError):
            ServiceConfig.from_dict(value)

    def test_host_must_be_loopback(self):
        with self.assertRaises(ConfigError):
            ServiceConfig(host="0.0.0.0")
        value = self._valid()
        value["service"]["host"] = "localhost"
        with self.assertRaises(ConfigError):
            ServiceConfig.from_dict(value)

    def test_port_bounds(self):
        for bad in (1023, 65536):
            with self.subTest(port=bad):
                with self.assertRaises(ConfigError):
                    ServiceConfig(port=bad)

    def test_port_boolean_rejected(self):
        with self.assertRaises(ConfigError):
            ServiceConfig(port=True)

    def test_cadence_bounds(self):
        for bad in (99, 60001):
            with self.subTest(cadence=bad):
                with self.assertRaises(ConfigError):
                    ServiceConfig(cadence_milliseconds=bad)

    def test_max_messages_must_be_100(self):
        with self.assertRaises(ConfigError):
            ServiceConfig(max_messages=50)


class ServicePolicyTests(unittest.TestCase):
    def test_service_derives_policy_from_config_when_none_passed(self):
        config = dataclasses.replace(
            ServiceConfig.default(),
            deny_user_ids=frozenset({"user:alice"}),
            gift_threshold_milli_cny=500,
        )
        service = Service(config)
        self.assertEqual(service.policy.deny_user_ids, frozenset({"user:alice"}))
        self.assertEqual(service.policy.gift_threshold_milli_cny, 500)

    def test_service_prefers_explicit_policy(self):
        explicit = FilteringPolicy(gift_threshold_milli_cny=777)
        service = Service(ServiceConfig.default(), policy=explicit)
        self.assertIs(service.policy, explicit)


class ServiceCompositionTests(unittest.TestCase):
    def test_host_timeline_capacity_constant_is_1000(self):
        self.assertEqual(HOST_TIMELINE_MAX_MESSAGES, 1000)

    def test_service_composes_1000_host_timeline_and_100_obs_capacity(self):
        service = Service(ServiceConfig.default(), clock=_fixed_clock)
        hub = service.hub
        self.assertEqual(hub.delivered_capacity, 100)
        for sequence in range(1, 1011):
            hub.publish(make_message(sequence))
        host = hub.snapshot()
        self.assertEqual(len(host), 1000)
        self.assertEqual(host[0].sequence, 11)
        self.assertEqual(host[-1].sequence, 1010)
        delivered = hub.filtered_snapshot()
        self.assertEqual(len(delivered), 100)
        self.assertEqual(delivered[0].sequence, 911)
        self.assertEqual(delivered[-1].sequence, 1010)


class ServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "index.html").write_text("<html>obs</html>", encoding="utf-8")
        (root / "app.js").write_text("// js", encoding="utf-8")
        (root / "style.css").write_text("/* css */", encoding="utf-8")
        self.root = root

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def _config(self, **kwargs):
        return dataclasses.replace(ServiceConfig.default(), **kwargs)

    async def _start(self, port=None, clock=None, **kwargs):
        config = self._config(
            port=port if port is not None else _free_port(), **kwargs
        )
        service = Service(config, asset_root=self.root, clock=clock)
        await service.start()
        return service

    def _url(self, service, path="/"):
        return f"http://127.0.0.1:{service.port}{path}"

    async def test_binds_loopback_and_serves_health(self):
        service = await self._start()
        try:
            self.assertEqual(service.host, "127.0.0.1")
            async with aiohttp.ClientSession() as sess:
                async with sess.get(self._url(service, "/health")) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(
                        await resp.json(), {"protocolVersion": 1, "status": "ok"}
                    )
        finally:
            await service.stop()

    async def test_bind_conflict_is_fatal(self):
        port = _free_port()
        first = Service(self._config(port=port), asset_root=self.root)
        await first.start()
        try:
            second = Service(self._config(port=port), asset_root=self.root)
            with self.assertRaises(OSError):
                await second.start()
        finally:
            await first.stop()

    async def test_ws_snapshot_then_increments(self):
        service = await self._start(cadence_milliseconds=60000)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as ws:
                    snapshot = json.loads((await ws.receive()).data)
                    self.assertEqual(snapshot["type"], "snapshot")
                    await ws.send_str(HELLO)
                    service.hub.publish(make_message(100))
                    service.hub.publish(make_message(101))
                    for expected in (100, 101):
                        frame = json.loads((await ws.receive()).data)
                        self.assertEqual(frame["type"], "message.created")
                        self.assertEqual(
                            frame["payload"]["message"]["sequence"], expected
                        )
        finally:
            await service.stop()

    async def test_multiple_clients_are_independent(self):
        service = await self._start(cadence_milliseconds=60000)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as first, sess.ws_connect(
                    self._url(service, "/ws")
                ) as second:
                    await first.receive()
                    await second.receive()
                    await first.send_str(HELLO)
                    await second.send_str(HELLO)
                    service.hub.publish(make_message(100))
                    for ws in (first, second):
                        frame = json.loads((await ws.receive()).data)
                        self.assertEqual(
                            frame["payload"]["message"]["sequence"], 100
                        )
        finally:
            await service.stop()

    async def test_reconnect_receives_replacement_snapshot(self):
        service = await self._start(cadence_milliseconds=60000, clock=_fixed_clock)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as ws:
                    await ws.receive()
                    await ws.send_str(HELLO)
                    service.hub.publish(make_message(100))
                    self.assertEqual(
                        json.loads((await ws.receive()).data)["payload"]["message"][
                            "sequence"
                        ],
                        100,
                    )

                async with sess.ws_connect(self._url(service, "/ws")) as reconnected:
                    snapshot = json.loads((await reconnected.receive()).data)
                    self.assertEqual(snapshot["type"], "snapshot")
                    self.assertIn(
                        100, [m["sequence"] for m in snapshot["payload"]["messages"]]
                    )
        finally:
            await service.stop()

    async def test_reconnect_snapshot_excludes_stale_messages(self):
        from datetime import datetime, timedelta, timezone

        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        def millis(value: str) -> int:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
            delta = parsed - epoch
            return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000

        def at(offset_millis: int) -> str:
            value = base + timedelta(milliseconds=offset_millis)
            return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}Z"

        # A fixed clock at base + 5 minutes: messages exactly five minutes old
        # stay in-window while older messages are trimmed.
        service = await self._start(
            cadence_milliseconds=60000, clock=lambda: millis(at(0)) + 300_000
        )
        try:
            for _ in range(1000):
                if len(service.hub.snapshot()) >= 1:
                    break
                await asyncio.sleep(0.005)
            service.hub.publish(make_message_at(100, at(-1)))
            service.hub.publish(make_message_at(101, at(0)))
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as ws:
                    snapshot = json.loads((await ws.receive()).data)
                    sequences = [
                        m["sequence"] for m in snapshot["payload"]["messages"]
                    ]
                    self.assertNotIn(100, sequences)
                    self.assertIn(101, sequences)
        finally:
            await service.stop()

    async def test_unknown_and_traversal_assets_404(self):
        service = await self._start()
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(self._url(service, "/assets/app.js")) as resp:
                    self.assertEqual(resp.status, 200)
                async with sess.get(self._url(service, "/assets/style.css")) as resp:
                    self.assertEqual(resp.status, 200)
                async with sess.get(self._url(service, "/assets/secret.txt")) as resp:
                    self.assertEqual(resp.status, 404)
                async with sess.get(self._url(service, "/assets/../etc/passwd")) as resp:
                    self.assertEqual(resp.status, 404)
        finally:
            await service.stop()

    async def test_shutdown_closes_websocket_with_1001(self):
        service = await self._start(cadence_milliseconds=60000)
        async with aiohttp.ClientSession() as sess:
            ws = await sess.ws_connect(self._url(service, "/ws"))
            await ws.receive()  # snapshot
            await ws.send_str(HELLO)
            await service.stop()
            msg = await ws.receive()
            self.assertEqual(msg.type, aiohttp.WSMsgType.CLOSE)
            self.assertEqual(msg.data, 1001)
            await ws.close()

    async def test_shutdown_releases_port(self):
        port = _free_port()
        service = await self._start(port=port)
        await service.stop()
        rebound = Service(self._config(port=port), asset_root=self.root)
        try:
            await rebound.start()
        finally:
            await rebound.stop()

    async def test_producer_streams_mock_messages(self):
        service = await self._start(cadence_milliseconds=100)
        try:
            for _ in range(200):
                if len(service.hub.snapshot()) >= 1:
                    break
                await asyncio.sleep(0.01)
            snapshot = service.hub.snapshot()
            self.assertGreaterEqual(len(snapshot), 1)
            self.assertEqual(snapshot[0].kind, "danmaku")
        finally:
            await service.stop()

    async def test_pending_gift_flushed_on_normal_shutdown(self):
        service = await self._start(cadence_milliseconds=60000)
        try:
            # Wait for the producer's first danmaku so the snapshot is stable.
            for _ in range(1000):
                if len(service.hub.snapshot()) >= 1:
                    break
                await asyncio.sleep(0.005)
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as ws:
                    snapshot = json.loads((await ws.receive()).data)
                    self.assertEqual(
                        [m["sequence"] for m in snapshot["payload"]["messages"]],
                        [1],
                    )
                    await ws.send_str(HELLO)
                    service.hub.publish(make_gift(100, quantity=2, amount_milli_cny=1000))
                    service.hub.publish(make_gift(101, quantity=3, amount_milli_cny=1500))
                    await service.stop()

                    frame = json.loads((await ws.receive()).data)
                    self.assertEqual(frame["type"], "message.created")
                    message = frame["payload"]["message"]
                    self.assertEqual(message["kind"], "gift")
                    self.assertEqual(message["sequence"], 100)
                    self.assertEqual(message["data"]["quantity"], 5)
                    self.assertEqual(message["data"]["totalAmountMilliCny"], 2500)

                    close = await ws.receive()
                    self.assertEqual(close.type, aiohttp.WSMsgType.CLOSE)
                    self.assertEqual(close.data, 1001)
                    await ws.close()
        finally:
            await service.stop()


if __name__ == "__main__":
    unittest.main()
