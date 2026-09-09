"""Focused tests for the independent host-monitoring delivery seam.

The host stream is the complete, original (un-aggregated, unfiltered) canonical
message stream in publish order. It is independent of the OBS delivery path:
host subscribers still receive messages the OBS filter suppresses, still
receive each gift before aggregation merges it, and their reconnect snapshot is
the full canonical host timeline (``DistributionHub.snapshot()``), not the
filtered/aggregated five-minute OBS snapshot.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.hub import (  # noqa: E402
    HOST_DELIVERY_CAPACITY,
    DistributionHub,
)
from danmaku.core.model import Message  # noqa: E402
from danmaku.core.snapshot import SnapshotStore  # noqa: E402

HOST_TIMELINE_MAX_MESSAGES = 1000


def _fixed_clock() -> int:
    """Deterministic clock that keeps every fixed 2026-01-01 fixture in-window."""
    return 0


def make_message(
    sequence: int,
    kind: str = "danmaku",
    user_id: str = "user:alice",
    name: str = "Alice",
    data: dict | None = None,
) -> Message:
    default_data = {
        "danmaku": {"text": "hello"},
        "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
        "guard": {"tier": "captain", "months": 1},
        "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
    }
    return Message.from_dict(
        {
            "id": f"host:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": user_id, "name": name},
            "data": data if data is not None else dict(default_data[kind]),
        }
    )


class HostDeliveryConstantTests(unittest.TestCase):
    def test_host_delivery_capacity_is_1000(self):
        self.assertEqual(HOST_DELIVERY_CAPACITY, 1000)

    def test_host_capacity_must_be_a_positive_integer(self):
        for bad in (0, -1, True, 1.0, "1000", None):
            with self.subTest(host_capacity=bad):
                with self.assertRaises(ValueError):
                    DistributionHub(host_capacity=bad)

    def test_host_capacity_defaults_to_1000(self):
        hub = DistributionHub()
        self.assertEqual(hub.host_capacity, 1000)

    def test_host_capacity_exposed_as_property(self):
        hub = DistributionHub(host_capacity=123)
        self.assertEqual(hub.host_capacity, 123)


class HostDeliveryStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_subscriber_receives_every_message_in_order(self):
        hub = DistributionHub(clock=_fixed_clock)
        subscriber = hub.host_subscribe()
        for sequence in range(1, 6):
            hub.publish(make_message(sequence))
        received = [(await subscriber.receive()).sequence for _ in range(5)]
        self.assertEqual(received, [1, 2, 3, 4, 5])

    async def test_host_stream_is_not_filtered(self):
        def suppress_alice(message: Message) -> bool:
            return message.user.id == "user:alice"

        hub = DistributionHub(filter=suppress_alice, clock=_fixed_clock)
        host_subscriber = hub.host_subscribe()
        obs_subscriber = hub.subscribe()

        hub.publish(make_message(1, user_id="user:alice"))
        hub.publish(make_message(2, user_id="user:bob", name="Bob"))

        # The host sees both messages; OBS never sees the suppressed one.
        self.assertEqual((await host_subscriber.receive()).sequence, 1)
        self.assertEqual((await host_subscriber.receive()).sequence, 2)
        self.assertEqual((await obs_subscriber.receive()).sequence, 2)

    async def test_host_stream_is_not_aggregated(self):
        hub = DistributionHub(clock=_fixed_clock)
        host_subscriber = hub.host_subscribe()
        obs_subscriber = hub.subscribe()

        gift = {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000}
        hub.publish(make_message(1, kind="gift", data=gift))
        hub.publish(
            make_message(
                2,
                kind="gift",
                data={"giftName": "Star", "quantity": 3, "totalAmountMilliCny": 1500},
            )
        )
        hub.finalize()

        # Host receives each raw gift; OBS receives the single merged gift.
        first = await host_subscriber.receive()
        self.assertEqual(first.sequence, 1)
        self.assertEqual(first.data["quantity"], 2)
        second = await host_subscriber.receive()
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.data["quantity"], 3)

        merged = await obs_subscriber.receive()
        self.assertEqual(merged.sequence, 1)
        self.assertEqual(merged.data["quantity"], 5)

    async def test_host_snapshot_is_complete_unfiltered_timeline(self):
        def suppress_alice(message: Message) -> bool:
            return message.user.id == "user:alice"

        store = SnapshotStore(max_messages=HOST_TIMELINE_MAX_MESSAGES)
        hub = DistributionHub(store=store, filter=suppress_alice, clock=_fixed_clock)
        hub.publish(make_message(1, user_id="user:alice"))
        hub.publish(make_message(2, user_id="user:bob", name="Bob"))

        host = hub.snapshot()
        self.assertEqual([m.sequence for m in host], [1, 2])

        delivered = hub.filtered_snapshot()
        self.assertEqual([m.sequence for m in delivered], [2])

    async def test_host_subscriber_queue_is_bounded_at_1000(self):
        hub = DistributionHub(clock=_fixed_clock)
        subscriber = hub.host_subscribe()
        for sequence in range(1, 1005):
            hub.publish(make_message(sequence))
        self.assertFalse(subscriber.closed)
        received = [(await subscriber.receive()).sequence for _ in range(1000)]
        self.assertEqual(received, list(range(5, 1005)))

    async def test_host_subscribers_are_independent_of_obs_subscribers(self):
        hub = DistributionHub(clock=_fixed_clock)
        host_subscriber = hub.host_subscribe()
        obs_subscriber = hub.subscribe()
        for sequence in range(1, 4):
            hub.publish(make_message(sequence))
        self.assertEqual(
            [(await host_subscriber.receive()).sequence for _ in range(3)],
            [1, 2, 3],
        )
        self.assertEqual(
            [(await obs_subscriber.receive()).sequence for _ in range(3)],
            [1, 2, 3],
        )


if __name__ == "__main__":
    unittest.main()
