"""End-to-end acceptance tests for the integrated first vertical slice.

Unlike the lower-layer tests, these drive the real loopback-only ``aiohttp``
``Service`` on an ephemeral port and observe messages that flowed end-to-end:

``MockSource -> DistributionHub -> aiohttp WebSocket -> protocol v1``

No ``TestClient``, hub stub, or manual ``publish`` is used. The producer is the
deterministic four-kind mock source, so the ordering assertions are stable
regardless of how many messages the snapshot had already captured by connect
time: any four consecutive increments are a rotation of the canonical kind
order and cover all four kinds.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import socket
import sys
import unittest
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.server.config import ServiceConfig  # noqa: E402
from danmaku.server.runner import Service  # noqa: E402

HELLO = '{"protocolVersion":1,"type":"hello","payload":{}}'

KIND_ORDER = ("danmaku", "gift", "guard", "superChat")

_KIND_EXPECTATIONS = {
    "danmaku": {
        "user_name": "Alice",
        "data": {"text": "Hello <OBS> & everyone"},
    },
    "gift": {
        "user_name": "Bob",
        "data": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
    },
    "guard": {
        "user_name": "Dana",
        "data": {"tier": "captain", "months": 1},
    },
    "superChat": {
        "user_name": "Carol",
        "data": {
            "text": "Great stream",
            "amountMilliCny": 30000,
            "durationSeconds": 60,
        },
    },
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class VerticalSliceE2ETests(unittest.IsolatedAsyncioTestCase):
    def _config(self, **kwargs):
        return dataclasses.replace(ServiceConfig.default(), **kwargs)

    def _url(self, service, path="/"):
        return f"http://127.0.0.1:{service.port}{path}"

    async def _start(self, **kwargs):
        config = self._config(port=kwargs.pop("port", _free_port()), **kwargs)
        service = Service(config)
        await service.start()
        return service

    async def test_observes_four_ordered_kinds_over_real_websocket(self):
        service = await self._start(cadence_milliseconds=100)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as ws:
                    snapshot = json.loads((await ws.receive()).data)
                    self.assertEqual(snapshot["protocolVersion"], 1)
                    self.assertEqual(snapshot["type"], "snapshot")

                    await ws.send_str(HELLO)

                    kinds = []
                    sequences = []
                    messages = []
                    for _ in range(4):
                        frame = json.loads(
                            (await asyncio.wait_for(ws.receive(), timeout=10)).data
                        )
                        self.assertEqual(frame["protocolVersion"], 1)
                        self.assertEqual(frame["type"], "message.created")
                        message = frame["payload"]["message"]
                        messages.append(message)
                        kinds.append(message["kind"])
                        sequences.append(message["sequence"])

                    self.assertEqual(
                        sequences,
                        list(range(sequences[0], sequences[0] + 4)),
                        "increments must arrive strictly in publish order",
                    )
                    self.assertEqual(set(kinds), set(KIND_ORDER))
                    rotations = {
                        tuple(KIND_ORDER[i:] + KIND_ORDER[:i]) for i in range(4)
                    }
                    self.assertIn(tuple(kinds), rotations)

                    for message in messages:
                        self.assertEqual(message["source"], "mock")
                        expectation = _KIND_EXPECTATIONS[message["kind"]]
                        self.assertEqual(
                            message["user"]["name"], expectation["user_name"]
                        )
                        self.assertEqual(message["data"], expectation["data"])
        finally:
            await service.stop()

    async def test_real_service_serves_health_and_packaged_obs_page(self):
        service = await self._start(cadence_milliseconds=60000)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(self._url(service, "/health")) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(
                        await resp.json(), {"protocolVersion": 1, "status": "ok"}
                    )
                async with sess.get(self._url(service, "/obs")) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.content_type, "text/html")
                    body = await resp.text()
                    self.assertIn("<!DOCTYPE html>", body)
                    self.assertIn('id="messages"', body)
        finally:
            await service.stop()


if __name__ == "__main__":
    unittest.main()
