"""Focused tests for the bounded OBS/client delivery backpressure policy.

The delivery queue boundary (``DistributionHub`` -> ``Subscription``) keeps each
client's queue independently bounded and FIFO for retained messages. Under
overload it evicts only the oldest queued ordinary danmaku to admit the new
message; gift, guard, and superChat are never evicted. A full queue with no
evictable danmaku fails closed through the existing slow-client close (1013)
rather than dropping a paid interaction.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.hub import (  # noqa: E402
    DistributionHub,
    Subscription,
    SubscriptionClosed,
)
from danmaku.core.model import Message  # noqa: E402


def make_message(sequence: int, kind: str = "danmaku") -> Message:
    data = {
        "danmaku": {"text": "hello"},
        "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
        "guard": {"tier": "captain", "months": 1},
        "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
    }[kind]
    return Message.from_dict(
        {
            "id": f"bp:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": "user:alice", "name": "Alice"},
            "data": data,
        }
    )


class _PassThrough:
    """An aggregator that emits every message unchanged (no gift merging)."""

    def accept(self, message: Message) -> tuple[Message, ...]:
        return (message,)

    def finalize(self) -> tuple[Message, ...]:
        return ()


class CapacityValidationTests(unittest.TestCase):
    def test_subscription_capacity_must_be_a_positive_integer(self):
        for bad in (0, -1, True, 1.0, "100", None):
            with self.subTest(capacity=bad):
                with self.assertRaises(ValueError):
                    Subscription(capacity=bad)

    def test_subscription_accepts_a_positive_integer_capacity(self):
        self.assertIsNotNone(Subscription(capacity=1))

    def test_hub_capacity_must_be_a_positive_integer(self):
        for bad in (0, -1, True, 1.0, "100", None):
            with self.subTest(capacity=bad):
                with self.assertRaises(ValueError):
                    DistributionHub(capacity=bad)


class BackpressurePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_queue_evicts_oldest_danmaku_to_admit_new_message(self):
        hub = DistributionHub(capacity=3)
        subscriber = hub.subscribe()
        for sequence in (1, 2, 3):
            hub.publish(make_message(sequence))
        # Queue is full [1, 2, 3]; the new danmaku evicts the oldest (1).
        hub.publish(make_message(4))
        self.assertFalse(subscriber.closed)
        received = [(await subscriber.receive()).sequence for _ in range(3)]
        self.assertEqual(received, [2, 3, 4])

    async def test_queue_stays_bounded_under_sustained_volume(self):
        hub = DistributionHub(capacity=2)
        subscriber = hub.subscribe()
        for sequence in range(1, 11):
            hub.publish(make_message(sequence))
        self.assertFalse(subscriber.closed)
        received = [(await subscriber.receive()).sequence for _ in range(2)]
        self.assertEqual(received, [9, 10])

    async def test_oldest_danmaku_is_evicted_not_oldest_paid_message(self):
        hub = DistributionHub(capacity=4)
        subscriber = hub.subscribe()
        hub.publish(make_message(1, "guard"))
        hub.publish(make_message(2))  # oldest danmaku
        hub.publish(make_message(3, "superChat"))
        hub.publish(make_message(4))  # danmaku
        hub.publish(make_message(5, "guard"))  # evicts danmaku 2, keeps paid 1
        self.assertFalse(subscriber.closed)
        received = [(await subscriber.receive()).sequence for _ in range(4)]
        self.assertEqual(received, [1, 3, 4, 5])

    async def test_paid_messages_are_never_evicted(self):
        hub = DistributionHub(capacity=3)
        subscriber = hub.subscribe()
        hub.publish(make_message(1, "guard"))
        hub.publish(make_message(2, "superChat"))
        hub.publish(make_message(3))  # ordinary danmaku
        hub.publish(make_message(4, "guard"))  # evicts danmaku 3, keeps paid
        self.assertFalse(subscriber.closed)
        received = [await subscriber.receive() for _ in range(3)]
        self.assertEqual([m.sequence for m in received], [1, 2, 4])
        self.assertEqual(
            [m.kind for m in received], ["guard", "superChat", "guard"]
        )

    async def test_full_paid_queue_fails_closed_with_1013(self):
        hub = DistributionHub(capacity=2)
        subscriber = hub.subscribe()
        hub.publish(make_message(1, "guard"))
        hub.publish(make_message(2, "superChat"))
        # Full queue has no evictable danmaku; the new paid message is refused.
        hub.publish(make_message(3, "guard"))
        self.assertTrue(subscriber.closed)
        self.assertEqual(subscriber.close_code, 1013)
        received = [await subscriber.receive() for _ in range(2)]
        self.assertEqual([m.sequence for m in received], [1, 2])
        with self.assertRaises(SubscriptionClosed) as caught:
            await subscriber.receive()
        self.assertEqual(caught.exception.code, 1013)

    async def test_gift_queue_fails_closed_without_dropping_paid(self):
        hub = DistributionHub(capacity=2, aggregator=_PassThrough())
        subscriber = hub.subscribe()
        hub.publish(make_message(1, "gift"))
        hub.publish(make_message(2, "gift"))
        hub.publish(make_message(3, "gift"))
        self.assertTrue(subscriber.closed)
        self.assertEqual(subscriber.close_code, 1013)
        received = [await subscriber.receive() for _ in range(2)]
        self.assertEqual([m.kind for m in received], ["gift", "gift"])

    async def test_full_paid_queue_fails_closed_even_for_incoming_danmaku(self):
        hub = DistributionHub(capacity=2)
        subscriber = hub.subscribe()
        hub.publish(make_message(1, "guard"))
        hub.publish(make_message(2, "superChat"))
        # No evictable danmaku is queued, so even an incoming danmaku cannot be
        # admitted without dropping a retained paid interaction.
        hub.publish(make_message(3))
        self.assertTrue(subscriber.closed)
        self.assertEqual(subscriber.close_code, 1013)

    async def test_eviction_is_per_client_and_canonical_store_stays_complete(self):
        hub = DistributionHub(capacity=2)
        slow = hub.subscribe()
        fast = hub.subscribe()
        hub.publish(make_message(1))
        hub.publish(make_message(2))
        self.assertEqual([(await fast.receive()).sequence for _ in range(2)], [1, 2])
        # Slow queue is full [1, 2]; publish 3 evicts 1 only from slow.
        hub.publish(make_message(3))
        self.assertFalse(slow.closed)
        self.assertEqual(
            [(await slow.receive()).sequence for _ in range(2)], [2, 3]
        )
        self.assertEqual((await fast.receive()).sequence, 3)
        self.assertEqual([m.sequence for m in hub.snapshot()], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
