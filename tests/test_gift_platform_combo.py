"""Focused tests for platform gift combo fidelity.

Covers the recorded-Bilibili platform combo identity and cumulative gift fields
end to end: adapter extraction of internal ``GiftPlatformMeta``, deterministic
aggregator grouping by combo identity with cumulative delta updates, the bounded
five-second sliding-window fallback for missing/invalid/unusable metadata, and
the guarantee that canonical v1 serialization exposes no new fields.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.bilibili import BilibiliAdapter, BilibiliAdapterError  # noqa: E402
from danmaku.core.aggregation import GiftAggregator  # noqa: E402
from danmaku.core.hub import DistributionHub  # noqa: E402
from danmaku.core.model import (  # noqa: E402
    MAX_SEQUENCE,
    GiftPlatformMeta,
    Message,
)
from danmaku.core.platform import GIFT_QUANTITY_MAX  # noqa: E402

FIXTURES = ROOT / "docs" / "bilibili-fixtures"

_BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def at(offset_millis: int) -> str:
    """RFC 3339 UTC timestamp ``offset_millis`` after the fixed base instant."""
    value = _BASE + timedelta(milliseconds=offset_millis)
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}Z"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def meta(
    combo_id: str = "combo-1",
    cumulative_quantity: int = 1,
    cumulative_amount_milli_cny: int = 500,
) -> GiftPlatformMeta:
    return GiftPlatformMeta(
        combo_id=combo_id,
        cumulative_quantity=cumulative_quantity,
        cumulative_amount_milli_cny=cumulative_amount_milli_cny,
    )


def make_gift(
    sequence: int,
    *,
    user_id: str = "user:bob",
    name: str = "Bob",
    gift_name: str = "Star",
    quantity: int = 1,
    amount_milli_cny: int = 500,
    received_at: str = "2026-01-01T00:00:00.000Z",
    platform_meta: GiftPlatformMeta | None = None,
) -> Message:
    return Message.from_dict(
        {
            "id": f"gift:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": received_at,
            "source": "bilibili",
            "kind": "gift",
            "user": {"id": user_id, "name": name},
            "data": {
                "giftName": gift_name,
                "quantity": quantity,
                "totalAmountMilliCny": amount_milli_cny,
            },
        },
        platform_meta=platform_meta,
    )


def make_non_gift(sequence: int, kind: str) -> Message:
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
            "source": "bilibili",
            "kind": kind,
            "user": {"id": "user:other", "name": "Someone"},
            "data": data,
        }
    )


class AdapterComboMetadataTests(unittest.TestCase):
    def test_combo_fixture_normalizes_platform_meta_and_per_event_data(self):
        message = BilibiliAdapter(start_sequence=1).normalize(_load("gift-combo.json"))
        self.assertEqual(message.id, "bilibili:g-200002")
        # The canonical per-event data carries the increment, not the cumulative.
        self.assertEqual(
            dict(message.data),
            {"giftName": "Star", "quantity": 1, "totalAmountMilliCny": 500},
        )
        self.assertIsNotNone(message.platform_meta)
        self.assertEqual(message.platform_meta.combo_id, "combo-200001")
        self.assertEqual(message.platform_meta.cumulative_quantity, 3)
        self.assertEqual(message.platform_meta.cumulative_amount_milli_cny, 1500)

    def test_plain_gift_fixture_has_no_platform_meta(self):
        message = BilibiliAdapter(start_sequence=1).normalize(_load("gift.json"))
        self.assertIsNone(message.platform_meta)

    def test_combo_metadata_is_never_serialized(self):
        message = BilibiliAdapter(start_sequence=1).normalize(_load("gift-combo.json"))
        serialized = message.to_dict()
        self.assertNotIn("comboId", serialized)
        self.assertNotIn("totalNum", serialized)
        self.assertNotIn("totalCoin", serialized)
        self.assertNotIn("platformMeta", serialized)
        self.assertEqual(
            set(serialized["data"]), {"giftName", "quantity", "totalAmountMilliCny"}
        )
        # Round-tripping through the canonical serialization drops the metadata.
        reparsed = Message.parse(message.to_json())
        self.assertIsNone(reparsed.platform_meta)
        self.assertEqual(reparsed.to_dict(), serialized)

    def test_partial_combo_metadata_rejected(self):
        record = _load("gift-combo.json")
        record["data"].pop("totalCoin")
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_combo_id_wrong_type_rejected(self):
        record = _load("gift-combo.json")
        record["data"]["comboId"] = 123
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_combo_id_control_character_rejected(self):
        record = _load("gift-combo.json")
        record["data"]["comboId"] = "combo\u0000id"
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_total_num_out_of_range_rejected(self):
        for bad in (0, GIFT_QUANTITY_MAX + 1):
            with self.subTest(totalNum=bad):
                record = _load("gift-combo.json")
                record["data"]["totalNum"] = bad
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(record)

    def test_total_coin_out_of_range_rejected(self):
        record = _load("gift-combo.json")
        record["data"]["totalCoin"] = MAX_SEQUENCE + 1
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_inconsistent_cumulative_falls_back_to_no_metadata(self):
        # Well-typed but contradictory: a cumulative quantity below the current
        # event's own contribution is unusable, so the adapter drops the
        # metadata (fallback) rather than fabricating a delta.
        record = _load("gift-combo.json")
        record["data"]["num"] = 3
        record["data"]["totalNum"] = 1  # below num=3
        message = BilibiliAdapter().normalize(record)
        self.assertIsNone(message.platform_meta)
        self.assertEqual(dict(message.data)["quantity"], 3)


class ComboGroupingTests(unittest.TestCase):
    def test_same_combo_id_merges_and_different_combo_id_finalizes(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, platform_meta=meta(combo_id="c1", cumulative_quantity=1, cumulative_amount_milli_cny=500), received_at=at(0)))
        self.assertEqual(
            aggregator.accept(
                make_gift(2, platform_meta=meta(combo_id="c1", cumulative_quantity=2, cumulative_amount_milli_cny=1000), received_at=at(1000))
            ),
            (),
        )
        emitted = aggregator.accept(
            make_gift(3, platform_meta=meta(combo_id="c2", cumulative_quantity=1, cumulative_amount_milli_cny=500), received_at=at(2000))
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["quantity"], 2)
        self.assertEqual(emitted[0].data["totalAmountMilliCny"], 1000)
        remaining = aggregator.finalize()
        self.assertEqual(remaining[0].data["quantity"], 1)

    def test_cumulative_values_are_not_double_counted(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, platform_meta=meta(cumulative_quantity=1, cumulative_amount_milli_cny=500), received_at=at(0)))
        aggregator.accept(make_gift(2, platform_meta=meta(cumulative_quantity=2, cumulative_amount_milli_cny=1000), received_at=at(1000)))
        aggregator.accept(make_gift(3, platform_meta=meta(cumulative_quantity=3, cumulative_amount_milli_cny=1500), received_at=at(2000)))
        emitted = aggregator.finalize()[0]
        # Final cumulative snapshot, not the naive sum of cumulative values (6, 3000).
        self.assertEqual(emitted.data["quantity"], 3)
        self.assertEqual(emitted.data["totalAmountMilliCny"], 1500)

    def test_combo_aggregate_is_a_plain_canonical_gift_without_metadata(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, platform_meta=meta(cumulative_quantity=1, cumulative_amount_milli_cny=500)))
        aggregator.accept(make_gift(2, platform_meta=meta(cumulative_quantity=2, cumulative_amount_milli_cny=1000)))
        emitted = aggregator.finalize()[0]
        self.assertIsNone(emitted.platform_meta)
        self.assertEqual(
            dict(emitted.data),
            {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
        )

    def test_combo_aggregate_keeps_first_gift_identity(self):
        first = make_gift(
            7,
            user_id="user:bob",
            name="Bob",
            platform_meta=meta(combo_id="c9", cumulative_quantity=1, cumulative_amount_milli_cny=500),
            received_at=at(1234),
        )
        aggregator = GiftAggregator()
        aggregator.accept(first)
        aggregator.accept(
            make_gift(8, platform_meta=meta(combo_id="c9", cumulative_quantity=2, cumulative_amount_milli_cny=1000), received_at=at(2000))
        )
        emitted = aggregator.finalize()[0]
        self.assertEqual(emitted.id, first.id)
        self.assertEqual(emitted.sequence, 7)
        self.assertEqual(emitted.received_at, at(1234))
        self.assertEqual(emitted.user.id, "user:bob")

    def test_combo_respects_the_sliding_window(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, platform_meta=meta(combo_id="c1", cumulative_quantity=1, cumulative_amount_milli_cny=500), received_at=at(0)))
        emitted = aggregator.accept(
            make_gift(2, platform_meta=meta(combo_id="c1", cumulative_quantity=2, cumulative_amount_milli_cny=1000), received_at=at(5001))
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["quantity"], 1)
        remaining = aggregator.finalize()
        self.assertEqual(remaining[0].data["quantity"], 2)


class ComboFallbackTests(unittest.TestCase):
    def test_missing_metadata_falls_back_to_sum_grouping(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, quantity=2, amount_milli_cny=1000))
        self.assertEqual(aggregator.accept(make_gift(2, quantity=3, amount_milli_cny=1500)), ())
        emitted = aggregator.finalize()[0]
        self.assertEqual(emitted.data["quantity"], 5)
        self.assertEqual(emitted.data["totalAmountMilliCny"], 2500)

    def test_non_monotonic_cumulative_is_unusable_and_falls_back(self):
        aggregator = GiftAggregator()
        aggregator.accept(
            make_gift(1, platform_meta=meta(combo_id="c1", cumulative_quantity=3, cumulative_amount_milli_cny=1500), received_at=at(0))
        )
        # The cumulative went backwards: unusable. Finalize the combo and fall
        # back to the plain per-event sum grouping for this event.
        emitted = aggregator.accept(
            make_gift(2, platform_meta=meta(combo_id="c1", cumulative_quantity=2, cumulative_amount_milli_cny=1000), received_at=at(1000))
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].data["quantity"], 3)
        self.assertEqual(emitted[0].data["totalAmountMilliCny"], 1500)
        remaining = aggregator.finalize()
        self.assertEqual(remaining[0].data["quantity"], 1)
        self.assertEqual(remaining[0].data["totalAmountMilliCny"], 500)


class ComboBoundaryTests(unittest.TestCase):
    def test_max_cumulative_values_are_representable(self):
        aggregator = GiftAggregator()
        aggregator.accept(
            make_gift(
                1,
                platform_meta=meta(
                    cumulative_quantity=GIFT_QUANTITY_MAX,
                    cumulative_amount_milli_cny=MAX_SEQUENCE,
                ),
            )
        )
        emitted = aggregator.finalize()[0]
        self.assertEqual(emitted.data["quantity"], GIFT_QUANTITY_MAX)
        self.assertEqual(emitted.data["totalAmountMilliCny"], MAX_SEQUENCE)
        # Still round-trips as a canonical v1 gift message.
        self.assertEqual(Message.parse(emitted.to_json()).to_dict(), emitted.to_dict())


class ComboOrderingTests(unittest.TestCase):
    def test_non_gift_flushes_combo_and_passes_through_unchanged(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, platform_meta=meta(combo_id="c1", cumulative_quantity=1, cumulative_amount_milli_cny=500)))
        guard = make_non_gift(2, "guard")
        emitted = aggregator.accept(guard)
        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[0].kind, "gift")
        self.assertEqual(emitted[1], guard)

    def test_combo_never_merges_with_other_gift_users_or_guards(self):
        aggregator = GiftAggregator()
        aggregator.accept(make_gift(1, platform_meta=meta(combo_id="c1", cumulative_quantity=1, cumulative_amount_milli_cny=500)))
        # A different user with a different combo id never merges.
        emitted = aggregator.accept(
            make_gift(2, user_id="user:carol", name="Carol", platform_meta=meta(combo_id="c2", cumulative_quantity=1, cumulative_amount_milli_cny=500))
        )
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].user.id, "user:bob")


class ComboHubIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_store_complete_and_obs_delivery_aggregated(self):
        hub = DistributionHub(clock=lambda: 0)
        subscriber = hub.subscribe()
        hub.publish(make_gift(1, platform_meta=meta(combo_id="c1", cumulative_quantity=1, cumulative_amount_milli_cny=500)))
        hub.publish(make_gift(2, platform_meta=meta(combo_id="c1", cumulative_quantity=2, cumulative_amount_milli_cny=1000)))
        hub.publish(make_non_gift(3, "guard"))
        hub.finalize()

        # Canonical state stays complete and un-aggregated (per-event data).
        canonical = hub.snapshot()
        self.assertEqual([m.sequence for m in canonical], [1, 2, 3])
        self.assertEqual(canonical[0].data["quantity"], 1)
        self.assertEqual(canonical[1].data["quantity"], 1)

        # OBS delivery shows the merged combo (cumulative) then the guard.
        delivered = hub.filtered_snapshot()
        self.assertEqual([m.sequence for m in delivered], [1, 3])
        self.assertEqual(delivered[0].data["quantity"], 2)
        self.assertEqual(delivered[0].data["totalAmountMilliCny"], 1000)

        received = await subscriber.receive()
        self.assertEqual(received.kind, "gift")
        self.assertEqual(received.data["quantity"], 2)

    async def test_host_stream_stays_raw_while_obs_shows_combo(self):
        hub = DistributionHub(clock=lambda: 0)
        host_subscriber = hub.host_subscribe()
        obs_subscriber = hub.subscribe()
        hub.publish(make_gift(1, platform_meta=meta(combo_id="c1", cumulative_quantity=1, cumulative_amount_milli_cny=500)))
        hub.publish(make_gift(2, platform_meta=meta(combo_id="c1", cumulative_quantity=2, cumulative_amount_milli_cny=1000)))
        hub.finalize()

        first_raw = await host_subscriber.receive()
        self.assertEqual(first_raw.data["quantity"], 1)
        second_raw = await host_subscriber.receive()
        self.assertEqual(second_raw.data["quantity"], 1)

        merged = await obs_subscriber.receive()
        self.assertEqual(merged.data["quantity"], 2)
        self.assertEqual(merged.data["totalAmountMilliCny"], 1000)


if __name__ == "__main__":
    unittest.main()
