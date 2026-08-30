"""Public-behavior tests for the bounded snapshot and ordered distribution hub."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.hub import DistributionHub, Subscription, SubscriptionClosed  # noqa: E402
from danmaku.core.model import Message  # noqa: E402
from danmaku.core.snapshot import (  # noqa: E402
    DuplicateIdError,
    NonIncreasingSequenceError,
    SnapshotStore,
)


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


def make_message_with(sequence, message_id, kind="danmaku"):
    data = {
        "danmaku": {"text": "hello"},
        "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
        "guard": {"tier": "captain", "months": 1},
        "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
    }[kind]
    return Message.from_dict(
        {
            "id": message_id,
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": "user:alice", "name": "Alice"},
            "data": data,
        }
    )


class SnapshotStoreTests(unittest.TestCase):
    def test_list_is_oldest_first(self):
        store = SnapshotStore(max_messages=100)
        for sequence in range(1, 5):
            store.append(make_message(sequence))
        self.assertEqual(
            [message.sequence for message in store.list()], [1, 2, 3, 4]
        )

    def test_retains_latest_100(self):
        store = SnapshotStore(max_messages=100)
        for sequence in range(1, 106):
            store.append(make_message(sequence))
        messages = store.list()
        self.assertEqual(len(messages), 100)
        self.assertEqual(messages[0].sequence, 6)
        self.assertEqual(messages[-1].sequence, 105)

    def test_list_returns_immutable_copy(self):
        store = SnapshotStore(max_messages=100)
        store.append(make_message(1))
        snapshot = store.list()
        self.assertIsInstance(snapshot, tuple)

    def test_rejects_non_message(self):
        store = SnapshotStore()
        with self.assertRaises(TypeError):
            store.append({"id": "x"})

    def test_rejects_duplicate_id(self):
        store = SnapshotStore()
        store.append(make_message(1))
        with self.assertRaises(DuplicateIdError):
            store.append(make_message_with(2, "test:0001"))

    def test_rejects_non_increasing_sequence(self):
        store = SnapshotStore()
        store.append(make_message_with(5, "a"))
        with self.assertRaises(NonIncreasingSequenceError):
            store.append(make_message_with(5, "b"))
        with self.assertRaises(NonIncreasingSequenceError):
            store.append(make_message_with(4, "c"))

    def test_evicted_id_can_be_reused_after_bound(self):
        store = SnapshotStore(max_messages=2)
        store.append(make_message_with(1, "old"))
        store.append(make_message_with(2, "b"))
        store.append(make_message_with(3, "c"))
        store.append(make_message_with(4, "old"))
        self.assertEqual([m.sequence for m in store.list()], [3, 4])


class DistributionHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_offers_in_order(self):
        hub = DistributionHub()
        subscriber = hub.subscribe()
        for sequence in range(1, 6):
            hub.publish(make_message(sequence))
        received = [(await subscriber.receive()).sequence for _ in range(5)]
        self.assertEqual(received, [1, 2, 3, 4, 5])

    async def test_independent_subscribers_receive_every_message(self):
        hub = DistributionHub()
        first = hub.subscribe()
        second = hub.subscribe()
        for sequence in range(1, 4):
            hub.publish(make_message(sequence))
        self.assertEqual(
            [(await first.receive()).sequence for _ in range(3)], [1, 2, 3]
        )
        self.assertEqual(
            [(await second.receive()).sequence for _ in range(3)], [1, 2, 3]
        )

    async def test_slow_subscriber_closed_with_1013_and_isolated(self):
        hub = DistributionHub(capacity=3)
        slow = hub.subscribe()
        fast = hub.subscribe()

        messages = [make_message(sequence) for sequence in range(1, 5)]

        for message in messages[:3]:
            hub.publish(message)

        fast_received = [await fast.receive() for _ in range(3)]

        hub.publish(messages[3])

        self.assertTrue(slow.closed)
        self.assertEqual(slow.close_code, 1013)

        self.assertEqual(
            [message.sequence for message in fast_received], [1, 2, 3]
        )
        self.assertEqual((await fast.receive()).sequence, 4)

        drained = [await slow.receive() for _ in range(3)]
        self.assertEqual([message.sequence for message in drained], [1, 2, 3])
        with self.assertRaises(SubscriptionClosed) as caught:
            await slow.receive()
        self.assertEqual(caught.exception.code, 1013)

    async def test_explicit_close_stops_receiving(self):
        hub = DistributionHub()
        subscriber = hub.subscribe()
        hub.publish(make_message(1))
        self.assertEqual((await subscriber.receive()).sequence, 1)
        subscriber.close(code=1000)
        self.assertTrue(subscriber.closed)
        with self.assertRaises(SubscriptionClosed):
            await subscriber.receive()

    async def test_publish_updates_store_snapshot(self):
        store = SnapshotStore(max_messages=2)
        hub = DistributionHub(store=store)
        for sequence in range(1, 4):
            hub.publish(make_message(sequence))
        self.assertEqual(
            [message.sequence for message in hub.snapshot()], [2, 3]
        )

    async def test_publish_rejects_non_message(self):
        hub = DistributionHub()
        with self.assertRaises(TypeError):
            hub.publish({"id": "x"})

    async def test_publish_rejects_duplicate_id_before_snapshot_and_broadcast(self):
        hub = DistributionHub()
        subscriber = hub.subscribe()
        hub.publish(make_message_with(1, "dup"))
        with self.assertRaises(DuplicateIdError):
            hub.publish(make_message_with(2, "dup"))
        self.assertEqual([m.sequence for m in hub.snapshot()], [1])
        hub.publish(make_message_with(2, "fresh"))
        self.assertEqual((await subscriber.receive()).sequence, 1)
        self.assertEqual((await subscriber.receive()).sequence, 2)

    async def test_publish_rejects_non_increasing_sequence(self):
        hub = DistributionHub()
        hub.publish(make_message_with(5, "a"))
        with self.assertRaises(NonIncreasingSequenceError):
            hub.publish(make_message_with(5, "b"))
        with self.assertRaises(NonIncreasingSequenceError):
            hub.publish(make_message_with(4, "c"))
        self.assertEqual([m.sequence for m in hub.snapshot()], [5])
        hub.publish(make_message_with(6, "d"))
        self.assertEqual([m.sequence for m in hub.snapshot()], [5, 6])

    async def test_publish_rejects_duplicate_against_existing_snapshot_state(self):
        store = SnapshotStore(max_messages=100)
        hub = DistributionHub(store=store)
        for sequence in range(1, 4):
            hub.publish(make_message(sequence))
        with self.assertRaises(DuplicateIdError):
            hub.publish(make_message_with(4, "test:0002"))
        self.assertEqual([m.sequence for m in hub.snapshot()], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
