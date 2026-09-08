"""Focused tests for the 1000-message canonical host timeline retention.

The canonical host state (``SnapshotStore`` and ``DistributionHub.snapshot()``)
retains the product-required most recent 1000 accepted messages in oldest-first
order, evicting only the oldest payload after message 1001. The OBS delivery
snapshot (``DistributionHub.filtered_snapshot()``) and per-client backpressure
queues stay independently capped at 100 messages with the existing five-minute
retention.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.hub import DistributionHub  # noqa: E402
from danmaku.core.model import Message  # noqa: E402
from danmaku.core.snapshot import DuplicateIdError, SnapshotStore  # noqa: E402

HOST_TIMELINE_MAX_MESSAGES = 1000


def make_message(sequence: int, message_id: str | None = None) -> Message:
    return Message.from_dict(
        {
            "id": message_id if message_id is not None else f"host:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": "danmaku",
            "user": {"id": "user:alice", "name": "Alice"},
            "data": {"text": "hello"},
        }
    )


def _fixed_clock() -> int:
    """Deterministic clock that keeps the fixed 2026-01-01 fixtures in-window."""
    return 0


class SnapshotStoreHostTimelineTests(unittest.TestCase):
    def test_retains_1000_messages_oldest_first(self):
        store = SnapshotStore(max_messages=HOST_TIMELINE_MAX_MESSAGES)
        for sequence in range(1, 1001):
            store.append(make_message(sequence))
        messages = store.list()
        self.assertEqual(len(messages), 1000)
        self.assertEqual(messages[0].sequence, 1)
        self.assertEqual(messages[-1].sequence, 1000)

    def test_evicts_only_oldest_payload_after_message_1001(self):
        store = SnapshotStore(max_messages=HOST_TIMELINE_MAX_MESSAGES)
        for sequence in range(1, 1002):
            store.append(make_message(sequence))
        messages = store.list()
        self.assertEqual(len(messages), 1000)
        self.assertEqual(messages[0].sequence, 2)
        self.assertEqual(messages[-1].sequence, 1001)

    def test_evicted_id_is_still_rejected(self):
        store = SnapshotStore(max_messages=HOST_TIMELINE_MAX_MESSAGES)
        for sequence in range(1, 1001):
            store.append(make_message(sequence))
        # Sequence 1's payload was evicted, but its id stays process-lifetime.
        with self.assertRaises(DuplicateIdError):
            store.append(make_message(1001, message_id="host:0001"))


class DistributionHubCapacityTests(unittest.TestCase):
    def test_delivered_capacity_must_be_a_positive_integer(self):
        for bad in (0, -1, True, 1.0, "100", None):
            with self.subTest(delivered_capacity=bad):
                with self.assertRaises(ValueError):
                    DistributionHub(delivered_capacity=bad)

    def test_delivered_capacity_exposed_as_property(self):
        hub = DistributionHub(delivered_capacity=100)
        self.assertEqual(hub.delivered_capacity, 100)

    def test_delivered_capacity_defaults_to_100(self):
        hub = DistributionHub()
        self.assertEqual(hub.delivered_capacity, 100)


class HostAndDeliveryCapacityTests(unittest.IsolatedAsyncioTestCase):
    def _hub(self) -> DistributionHub:
        return DistributionHub(
            store=SnapshotStore(max_messages=HOST_TIMELINE_MAX_MESSAGES),
            capacity=100,
            delivered_capacity=100,
            clock=_fixed_clock,
        )

    async def test_host_retains_1000_while_delivery_stays_at_100(self):
        hub = self._hub()
        for sequence in range(1, 1051):
            hub.publish(make_message(sequence))

        host = hub.snapshot()
        self.assertEqual(len(host), 1000)
        self.assertEqual(host[0].sequence, 51)
        self.assertEqual(host[-1].sequence, 1050)

        delivered = hub.filtered_snapshot()
        self.assertEqual(len(delivered), 100)
        self.assertEqual(delivered[0].sequence, 951)
        self.assertEqual(delivered[-1].sequence, 1050)

    async def test_subscriber_queue_capacity_is_independent(self):
        hub = self._hub()
        subscriber = hub.subscribe()
        # Publish far more than the per-client 100 queue bound; the queue
        # evicts oldest ordinary danmaku and stays bounded while the canonical
        # host snapshot retains the full host timeline.
        for sequence in range(1, 110):
            hub.publish(make_message(sequence))
        self.assertFalse(subscriber.closed)
        received = [(await subscriber.receive()).sequence for _ in range(100)]
        self.assertEqual(received, list(range(10, 110)))
        self.assertEqual(len(hub.snapshot()), 109)


if __name__ == "__main__":
    unittest.main()
