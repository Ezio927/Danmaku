"""Focused tests for deterministic gift combo aggregation.

Covers the pure-core :class:`GiftAggregator` abstraction and its integration at
the :class:`DistributionHub` producer/distribution seam. Service-level flush
behaviour on normal shutdown lives in ``test_service.py``.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.aggregation import (  # noqa: E402
    DEFAULT_WINDOW_MILLISECONDS,
    GiftAggregator,
)
from danmaku.core.hub import DistributionHub  # noqa: E402
from danmaku.core.model import MAX_SEQUENCE, Message  # noqa: E402

_BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def at(offset_millis: int) -> str:
    """RFC 3339 UTC timestamp ``offset_millis`` after the fixed base instant."""
    value = _BASE + timedelta(milliseconds=offset_millis)
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}Z"


def make_gift(
    sequence: int,
    *,
    user_id: str = "user:bob",
    name: str = "Bob",
    gift_name: str = "Star",
    quantity: int = 1,
    amount_milli_cny: int = 1000,
    received_at: str = "2026-01-01T00:00:00.000Z",
    source: str = "mock",
) -> Message:
    return Message.from_dict(
        {
            "id": f"gift:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": received_at,
            "source": source,
            "kind": "gift",
            "user": {"id": user_id, "name": name},
            "data": {
                "giftName": gift_name,
                "quantity": quantity,
                "totalAmountMilliCny": amount_milli_cny,
            },
        }
    )


def make_non_gift(sequence: int, kind: str = "danmaku") -> Message:
    data = {
        "danmaku": {"text": "hello"},
        "guard": {"tier": "captain", "months": 1},
        "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
    }[kind]
    return Message.from_dict(
        {
            "id": f"other:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": at(sequence * 1000),
            "source": "mock",
            "kind": kind,
            "user": {"id": f"user:{kind}", "name": "Someone"},
            "data": data,
        }
    )


class GiftAggregatorTests(unittest.TestCase):
    def test_window_constant_is_exactly_five_seconds(self):
        self.assertEqual(DEFAULT_WINDOW_MILLISECONDS, 5000)

    def test_non_gift_messages_pass_through_unchanged(self):
        aggregator = GiftAggregator()
        for kind in ("danmaku", "guard", "superChat"):
            with self.subTest(kind=kind):
                message = make_non_gift(1, kind)
                self.assertEqual(aggregator.accept(message), (message,))

    def test_non_gift_never_merges_with_gift_or_each_other(self):
        aggregator = GiftAggregator()
        self.assertEqual(aggregator.accept(make_gift(1)), ())
        emitted = aggregator.accept(make_non_gift(2, "guard"))
        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[0].kind, "gift")
        self.assertEqual(emitted[1].kind, "guard")

    def test_single_gift_waits_for_explicit_finalize(self):
        aggregator = GiftAggregator()
        self.assertEqual(aggregator.accept(make_gift(1)), ())
        self.assertTrue(aggregator.has_pending)
        emitted = aggregator.finalize()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["quantity"], 1)
        self.assertFalse(aggregator.has_pending)

    def test_finalize_is_idempotent(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1))
        self.assertEqual(len(aggregator.finalize()), 1)
        self.assertEqual(aggregator.finalize(), ())

    def test_matching_gifts_merge_quantity_and_amount(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, quantity=2, amount_milli_cny=1000))
        self.assertEqual(
            aggregator.accept(make_gift(2, quantity=3, amount_milli_cny=1500)), ()
        )
        emitted = aggregator.finalize()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["quantity"], 5)
        self.assertEqual(emitted[0].data["totalAmountMilliCny"], 2500)

    def test_new_matching_gift_extends_window(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, received_at=at(0)))
        # 4.0s later: within window, merges.
        aggregator.accept(make_gift(2, received_at=at(4000)))
        # 4.0s after the new anchor: still within the extended window.
        self.assertEqual(aggregator.accept(make_gift(3, received_at=at(8000))), ())
        emitted = aggregator.finalize()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["quantity"], 3)

    def test_exact_five_second_boundary_merges(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, received_at=at(0)))
        self.assertEqual(aggregator.accept(make_gift(2, received_at=at(5000))), ())
        emitted = aggregator.finalize()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["quantity"], 2)

    def test_beyond_five_seconds_starts_a_later_group(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, received_at=at(0)))
        emitted = aggregator.accept(make_gift(2, received_at=at(5001)))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].sequence, 1)
        self.assertEqual(emitted[0].data["quantity"], 1)
        remaining = aggregator.finalize()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].sequence, 2)

    def test_different_user_finalizes_and_does_not_merge(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, user_id="user:bob"))
        emitted = aggregator.accept(make_gift(2, user_id="user:carol", name="Carol"))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].user.id, "user:bob")
        self.assertEqual(emitted[0].data["quantity"], 1)
        remaining = aggregator.finalize()
        self.assertEqual(remaining[0].user.id, "user:carol")

    def test_different_gift_name_finalizes_and_does_not_merge(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, gift_name="Star"))
        emitted = aggregator.accept(make_gift(2, gift_name="Moon"))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["giftName"], "Star")
        remaining = aggregator.finalize()
        self.assertEqual(remaining[0].data["giftName"], "Moon")

    def test_aggregate_keeps_first_gift_identity(self):
        aggregator = GiftAggregator()
        first = make_gift(
            7,
            user_id="user:bob",
            name="Bob",
            gift_name="Star",
            quantity=2,
            amount_milli_cny=1000,
            received_at=at(1234),
            source="bilibili",
        )
        aggregator.accept(first)
        aggregator.accept(
            make_gift(8, quantity=1, received_at=at(2000), source="bilibili")
        )
        emitted = aggregator.finalize()[0]
        self.assertEqual(emitted.id, first.id)
        self.assertEqual(emitted.sequence, 7)
        self.assertEqual(emitted.received_at, at(1234))
        self.assertEqual(emitted.source, "bilibili")
        self.assertEqual(emitted.user.id, "user:bob")
        self.assertEqual(emitted.user.name, "Bob")

    def test_aggregate_is_a_valid_canonical_gift_message(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, quantity=2, amount_milli_cny=1000))
        aggregator.accept(make_gift(2, quantity=3, amount_milli_cny=1500))
        emitted = aggregator.finalize()[0]
        self.assertEqual(emitted.kind, "gift")
        self.assertEqual(
            dict(emitted.data),
            {
                "giftName": "Star",
                "quantity": 5,
                "totalAmountMilliCny": 2500,
            },
        )
        # Round-trips through the canonical serialization unchanged.
        self.assertEqual(
            Message.parse(emitted.to_json()).to_dict(), emitted.to_dict()
        )

    def test_quantity_overflow_splits_into_a_new_group(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, quantity=1_000_000))
        emitted = aggregator.accept(make_gift(2, quantity=1))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["quantity"], 1_000_000)
        remaining = aggregator.finalize()
        self.assertEqual(remaining[0].data["quantity"], 1)

    def test_amount_overflow_splits_into_a_new_group(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, quantity=1, amount_milli_cny=MAX_SEQUENCE))
        emitted = aggregator.accept(make_gift(2, quantity=1, amount_milli_cny=1))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["totalAmountMilliCny"], MAX_SEQUENCE)
        remaining = aggregator.finalize()
        self.assertEqual(remaining[0].data["totalAmountMilliCny"], 1)

    def test_window_must_be_a_non_negative_integer(self):
        GiftAggregator(window_milliseconds=0)
        with self.assertRaises(TypeError):
            GiftAggregator(window_milliseconds=True)
        with self.assertRaises(TypeError):
            GiftAggregator(window_milliseconds="5000")
        with self.assertRaises(ValueError):
            GiftAggregator(window_milliseconds=-1)

    def test_accept_rejects_non_message(self):
        with self.assertRaises(TypeError):
            GiftAggregator().accept({"id": "x"})


class DistributionHubAggregationTests(unittest.IsolatedAsyncioTestCase):
    async def test_consecutive_gifts_aggregate_on_delivery(self):
        hub = DistributionHub()
        subscriber = hub.subscribe()
        hub.publish(make_gift(1, quantity=2, amount_milli_cny=1000))
        hub.publish(make_gift(2, quantity=3, amount_milli_cny=1500))
        hub.publish(make_non_gift(3, "guard"))

        self.assertEqual([m.sequence for m in hub.snapshot()], [1, 2, 3])

        first = await subscriber.receive()
        self.assertEqual(first.kind, "gift")
        self.assertEqual(first.data["quantity"], 5)
        self.assertEqual(first.data["totalAmountMilliCny"], 2500)
        second = await subscriber.receive()
        self.assertEqual(second.kind, "guard")
        self.assertEqual(second.sequence, 3)

        delivered = hub.filtered_snapshot()
        self.assertEqual([m.sequence for m in delivered], [1, 3])
        self.assertEqual(delivered[0].data["quantity"], 5)

    async def test_canonical_store_stays_unaggregated(self):
        hub = DistributionHub()
        hub.publish(make_gift(1, quantity=2, amount_milli_cny=1000))
        hub.publish(make_gift(2, quantity=3, amount_milli_cny=1500))
        hub.finalize()
        canonical = hub.snapshot()
        self.assertEqual(len(canonical), 2)
        self.assertEqual(canonical[0].data["quantity"], 2)
        self.assertEqual(canonical[1].data["quantity"], 3)
        delivered = hub.filtered_snapshot()
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].data["quantity"], 5)

    async def test_finalize_flushes_pending_to_delivery(self):
        hub = DistributionHub()
        subscriber = hub.subscribe()
        hub.publish(make_gift(1))
        self.assertEqual(hub.filtered_snapshot(), ())
        hub.finalize()
        self.assertEqual((await subscriber.receive()).sequence, 1)
        self.assertEqual([m.sequence for m in hub.filtered_snapshot()], [1])

    async def test_delivery_sequence_stays_strictly_increasing(self):
        hub = DistributionHub()
        subscriber = hub.subscribe()
        hub.publish(make_non_gift(1, "danmaku"))
        hub.publish(make_gift(2))
        hub.publish(make_gift(3))
        hub.publish(make_non_gift(4, "superChat"))
        hub.publish(make_gift(5, gift_name="Moon"))
        hub.finalize()

        received = [(await subscriber.receive()).sequence for _ in range(4)]
        self.assertEqual(received, [1, 2, 4, 5])
        self.assertEqual(
            [m.sequence for m in hub.filtered_snapshot()], [1, 2, 4, 5]
        )

    async def test_aggregate_then_filter_uses_combined_amount(self):
        def below_thousand(message: Message) -> bool:
            return (
                message.kind == "gift"
                and message.data["totalAmountMilliCny"] < 1000
            )

        hub = DistributionHub(filter=below_thousand)
        subscriber = hub.subscribe()
        hub.publish(make_gift(1, amount_milli_cny=600))
        hub.publish(make_gift(2, amount_milli_cny=600))
        hub.finalize()

        # Each gift alone is below 1000, but the combined aggregate reaches
        # 1200, proving aggregation runs before delivery filtering.
        delivered = hub.filtered_snapshot()
        self.assertEqual([m.data["totalAmountMilliCny"] for m in delivered], [1200])
        self.assertEqual(
            (await subscriber.receive()).data["totalAmountMilliCny"], 1200
        )

    async def test_aggregator_must_expose_accept_and_finalize(self):
        with self.assertRaises(TypeError):
            DistributionHub(aggregator=object())


if __name__ == "__main__":
    unittest.main()
