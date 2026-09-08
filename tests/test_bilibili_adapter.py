"""Public-behavior tests for the offline recorded-Bilibili event adapter."""

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.bilibili import (  # noqa: E402
    BilibiliAdapter,
    BilibiliAdapterError,
    DuplicateEventError,
)
from danmaku.core.snapshot import SnapshotStore  # noqa: E402

FIXTURES = ROOT / "docs" / "bilibili-fixtures"

MAX_SEQUENCE = 9_007_199_254_740_991

_DATA = {
    "DANMU_MSG": {"text": "hello"},
    "SEND_GIFT": {"giftName": "Star", "num": 2, "unitPriceMilliCny": 500},
    "GUARD_BUY": {"guardLevel": 1, "num": 1},
    "SUPER_CHAT_MESSAGE": {"message": "hi", "priceMilliCny": 30000, "time": 60},
}


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _record(cmd="DANMU_MSG", data=None, **overrides):
    uid = overrides.pop("uid", "10001")
    uname = overrides.pop("uname", "Alice")
    record = {
        "cmd": cmd,
        "eventId": "d-100001",
        "timestamp": 1735689600000,
        "user": {"uid": uid, "uname": uname},
        "data": data if data is not None else dict(_DATA.get(cmd, _DATA["DANMU_MSG"])),
    }
    record.update(overrides)
    return record


class FixtureTests(unittest.TestCase):
    def test_each_fixture_normalizes_to_expected_canonical_message(self):
        expected = {
            "danmaku.json": {
                "id": "bilibili:d-100001",
                "sequence": 1,
                "receivedAt": "2025-01-01T00:00:00.000Z",
                "source": "bilibili",
                "kind": "danmaku",
                "user": {"id": "10001", "name": "Alice"},
                "data": {"text": "主播好！"},
            },
            "gift.json": {
                "id": "bilibili:g-200001",
                "sequence": 1,
                "receivedAt": "2025-01-01T00:00:01.000Z",
                "source": "bilibili",
                "kind": "gift",
                "user": {"id": "10002", "name": "Bob"},
                "data": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
            },
            "guard.json": {
                "id": "bilibili:g-300001",
                "sequence": 1,
                "receivedAt": "2025-01-01T00:00:02.000Z",
                "source": "bilibili",
                "kind": "guard",
                "user": {"id": "10003", "name": "Dana"},
                "data": {"tier": "captain", "months": 1},
            },
            "super-chat.json": {
                "id": "bilibili:s-400001",
                "sequence": 1,
                "receivedAt": "2025-01-01T00:00:03.000Z",
                "source": "bilibili",
                "kind": "superChat",
                "user": {"id": "10004", "name": "Carol"},
                "data": {"text": "Great stream", "amountMilliCny": 30000, "durationSeconds": 60},
            },
        }
        for name, expected_message in expected.items():
            with self.subTest(fixture=name):
                message = BilibiliAdapter(start_sequence=1).normalize(_load(name))
                self.assertEqual(message.to_dict(), expected_message)


class NormalizationTests(unittest.TestCase):
    def test_danmaku_maps_identity_name_timestamp_and_text(self):
        message = BilibiliAdapter().normalize(_record("DANMU_MSG"))
        self.assertEqual(message.source, "bilibili")
        self.assertEqual(message.kind, "danmaku")
        self.assertEqual(message.id, "bilibili:d-100001")
        self.assertEqual(message.user.id, "10001")
        self.assertEqual(message.user.name, "Alice")
        self.assertEqual(message.received_at, "2025-01-01T00:00:00.000Z")
        self.assertEqual(dict(message.data), {"text": "hello"})

    def test_gift_maps_identity_quantity_and_exact_integer_total(self):
        message = BilibiliAdapter().normalize(
            _record(
                "SEND_GIFT",
                data={"giftName": "Star", "num": 2, "unitPriceMilliCny": 500},
            )
        )
        self.assertEqual(message.kind, "gift")
        self.assertEqual(
            dict(message.data),
            {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
        )
        self.assertIsInstance(message.data["totalAmountMilliCny"], int)

    def test_guard_maps_level_to_tier_and_num_to_months(self):
        message = BilibiliAdapter().normalize(
            _record("GUARD_BUY", data={"guardLevel": 3, "num": 12})
        )
        self.assertEqual(message.kind, "guard")
        self.assertEqual(dict(message.data), {"tier": "governor", "months": 12})
        self.assertNotIn("totalAmountMilliCny", message.data)

    def test_guard_is_normalized_as_guard_not_ordinary_gift(self):
        message = BilibiliAdapter().normalize(_record("GUARD_BUY"))
        self.assertEqual(message.kind, "guard")
        self.assertNotEqual(message.kind, "gift")
        self.assertNotIn("totalAmountMilliCny", message.data)

    def test_super_chat_maps_text_amount_and_duration(self):
        message = BilibiliAdapter().normalize(
            _record(
                "SUPER_CHAT_MESSAGE",
                data={"message": "hi", "priceMilliCny": 30000, "time": 60},
            )
        )
        self.assertEqual(message.kind, "superChat")
        self.assertEqual(
            dict(message.data),
            {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
        )


class IdentityAndSequenceTests(unittest.TestCase):
    def test_id_is_deterministic_prefix_plus_event_id(self):
        message = BilibiliAdapter().normalize(_record(eventId="evt-42"))
        self.assertEqual(message.id, "bilibili:evt-42")

    def test_sequences_are_strictly_increasing(self):
        adapter = BilibiliAdapter(start_sequence=10)
        messages = [
            adapter.normalize(_record(eventId=f"e{i}")) for i in range(5)
        ]
        self.assertEqual([m.sequence for m in messages], [10, 11, 12, 13, 14])
        self.assertEqual(adapter.next_sequence, 15)

    def test_start_sequence_seam_shifts_output(self):
        message = BilibiliAdapter(start_sequence=7).normalize(_record())
        self.assertEqual(message.sequence, 7)

    def test_deterministic_across_instances(self):
        record = _load("gift.json")
        first = BilibiliAdapter().normalize(record).to_dict()
        second = BilibiliAdapter().normalize(record).to_dict()
        self.assertEqual(first, second)

    def test_duplicate_event_id_rejected_explicitly(self):
        adapter = BilibiliAdapter()
        adapter.normalize(_record(eventId="dup-1"))
        with self.assertRaises(DuplicateEventError):
            adapter.normalize(_record(eventId="dup-1"))

    def test_duplicate_rejection_does_not_consume_a_sequence(self):
        adapter = BilibiliAdapter(start_sequence=1)
        adapter.normalize(_record(eventId="a"))
        with self.assertRaises(DuplicateEventError):
            adapter.normalize(_record(eventId="a"))
        self.assertEqual(adapter.next_sequence, 2)
        self.assertEqual(adapter.normalize(_record(eventId="b")).sequence, 2)


class RejectionTests(unittest.TestCase):
    def test_unknown_cmd_rejected(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(_record(cmd="COMBO_SEND"))

    def test_non_string_cmd_rejected(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(_record(cmd=123))

    def test_missing_top_level_key_rejected(self):
        record = _record()
        del record["eventId"]
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_unknown_top_level_key_rejected(self):
        record = _record()
        record["secret"] = "x"
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_missing_user_key_rejected(self):
        record = _record()
        del record["user"]["uid"]
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_unknown_user_key_rejected(self):
        record = _record()
        record["user"]["avatar"] = "x"
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_missing_data_key_rejected(self):
        record = _record("DANMU_MSG")
        del record["data"]["text"]
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_unknown_data_key_rejected(self):
        record = _record("DANMU_MSG")
        record["data"]["extra"] = "x"
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_non_object_record_rejected(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize("not a record")

    def test_non_object_user_rejected(self):
        record = _record()
        record["user"] = ["10001", "Alice"]
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_non_object_data_rejected(self):
        record = _record()
        record["data"] = ["hello"]
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)


class TimestampTests(unittest.TestCase):
    def test_wrong_type_timestamp_rejected(self):
        for bad in (True, 1.0, "1735689600000", None):
            with self.subTest(timestamp=bad):
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(_record(timestamp=bad))

    def test_negative_timestamp_rejected(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(_record(timestamp=-1))

    def test_out_of_range_timestamp_rejected(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(_record(timestamp=MAX_SEQUENCE))

    def test_millisecond_precision_is_preserved(self):
        message = BilibiliAdapter().normalize(
            _record(timestamp=1735689600123)
        )
        self.assertEqual(message.received_at, "2025-01-01T00:00:00.123Z")


class StringRuleTests(unittest.TestCase):
    def test_uid_must_match_id_pattern(self):
        for bad in ("_x", "", "x" * 65):
            with self.subTest(uid=bad):
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(_record(uid=bad))
        BilibiliAdapter().normalize(_record(uid="x" * 64))

    def test_uname_bounds(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(_record(uname=""))
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(_record(uname="x" * 65))
        BilibiliAdapter().normalize(_record(uname="x" * 64))

    def test_control_character_in_uname_rejected(self):
        record = _record()
        record["user"]["uname"] = "Ali\u0000ce"
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)

    def test_text_bounds(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(_record("DANMU_MSG", data={"text": ""}))
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(_record("DANMU_MSG", data={"text": "x" * 501}))
        BilibiliAdapter().normalize(_record("DANMU_MSG", data={"text": "x" * 500}))


class GiftValidationTests(unittest.TestCase):
    def test_quantity_bounds(self):
        for bad in (0, -1, 1_000_001):
            with self.subTest(num=bad):
                record = _record(
                    "SEND_GIFT",
                    data={"giftName": "Star", "num": bad, "unitPriceMilliCny": 500},
                )
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(record)

    def test_unit_price_must_be_non_negative_integer(self):
        for bad in (-1, True, 500.0, "500"):
            with self.subTest(unitPriceMilliCny=bad):
                record = _record(
                    "SEND_GIFT",
                    data={"giftName": "Star", "num": 1, "unitPriceMilliCny": bad},
                )
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(record)

    def test_gift_total_overflow_rejected(self):
        record = _record(
            "SEND_GIFT",
            data={"giftName": "Star", "num": 1_000_000, "unitPriceMilliCny": MAX_SEQUENCE},
        )
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize(record)


class GuardValidationTests(unittest.TestCase):
    def test_unsupported_level_rejected(self):
        for bad in (0, 4, "1", 1.0, True):
            with self.subTest(guardLevel=bad):
                record = _record("GUARD_BUY", data={"guardLevel": bad, "num": 1})
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(record)

    def test_months_bounds(self):
        for bad in (0, 121):
            with self.subTest(num=bad):
                record = _record("GUARD_BUY", data={"guardLevel": 1, "num": bad})
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(record)
        record = _record("GUARD_BUY", data={"guardLevel": 1, "num": 120})
        BilibiliAdapter().normalize(record)


class SuperChatValidationTests(unittest.TestCase):
    def test_amount_bounds(self):
        for bad in (0, -1, True, 30000.0):
            with self.subTest(priceMilliCny=bad):
                record = _record(
                    "SUPER_CHAT_MESSAGE",
                    data={"message": "hi", "priceMilliCny": bad, "time": 60},
                )
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(record)
        record = _record(
            "SUPER_CHAT_MESSAGE",
            data={"message": "hi", "priceMilliCny": MAX_SEQUENCE, "time": 60},
        )
        BilibiliAdapter().normalize(record)

    def test_duration_bounds(self):
        for bad in (0, 86_401):
            with self.subTest(time=bad):
                record = _record(
                    "SUPER_CHAT_MESSAGE",
                    data={"message": "hi", "priceMilliCny": 1000, "time": bad},
                )
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(record)
        record = _record(
            "SUPER_CHAT_MESSAGE",
            data={"message": "hi", "priceMilliCny": 1000, "time": 86_400},
        )
        BilibiliAdapter().normalize(record)


class MoneyTypeTests(unittest.TestCase):
    def test_money_never_silently_coerced(self):
        cases = [
            ("SEND_GIFT", {"giftName": "Star", "num": 2.0, "unitPriceMilliCny": 500}),
            ("SEND_GIFT", {"giftName": "Star", "num": 2, "unitPriceMilliCny": 500.0}),
            ("SEND_GIFT", {"giftName": "Star", "num": "2", "unitPriceMilliCny": 500}),
            ("SUPER_CHAT_MESSAGE", {"message": "hi", "priceMilliCny": 30000.0, "time": 60}),
            ("SUPER_CHAT_MESSAGE", {"message": "hi", "priceMilliCny": "30000", "time": 60}),
            ("SUPER_CHAT_MESSAGE", {"message": "hi", "priceMilliCny": True, "time": 60}),
        ]
        for cmd, data in cases:
            with self.subTest(cmd=cmd, data=data):
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(_record(cmd, data=data))

    def test_non_finite_money_rejected(self):
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(price=bad):
                record = _record(
                    "SUPER_CHAT_MESSAGE",
                    data={"message": "hi", "priceMilliCny": bad, "time": 60},
                )
                with self.assertRaises(BilibiliAdapterError):
                    BilibiliAdapter().normalize(record)


class JsonParsingTests(unittest.TestCase):
    def test_normalize_json_accepts_valid_record(self):
        message = BilibiliAdapter().normalize_json(json.dumps(_load("danmaku.json")))
        self.assertEqual(message.kind, "danmaku")
        self.assertEqual(message.source, "bilibili")

    def test_normalize_json_rejects_non_finite_money(self):
        text = (
            '{"cmd":"SEND_GIFT","eventId":"g-1","timestamp":1735689600000,'
            '"user":{"uid":"1","uname":"A"},'
            '"data":{"giftName":"Star","num":2,"unitPriceMilliCny":NaN}}'
        )
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize_json(text)

    def test_normalize_json_rejects_invalid_json(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize_json("{not json")

    def test_normalize_json_requires_string(self):
        with self.assertRaises(BilibiliAdapterError):
            BilibiliAdapter().normalize_json({"cmd": "DANMU_MSG"})


class ConstructorTests(unittest.TestCase):
    def test_invalid_start_sequence_rejected(self):
        for bad in (0, -1, True, 1.0):
            with self.subTest(start_sequence=bad):
                with self.assertRaises(ValueError):
                    BilibiliAdapter(start_sequence=bad)


class SnapshotIntegrationTests(unittest.TestCase):
    def test_normalized_messages_feed_snapshot_store(self):
        adapter = BilibiliAdapter()
        store = SnapshotStore(max_messages=100)
        for name in ("danmaku.json", "gift.json", "guard.json", "super-chat.json"):
            store.append(adapter.normalize(_load(name)))
        self.assertEqual([m.sequence for m in store.list()], [1, 2, 3, 4])
        self.assertEqual(
            [m.id for m in store.list()],
            [
                "bilibili:d-100001",
                "bilibili:g-200001",
                "bilibili:g-300001",
                "bilibili:s-400001",
            ],
        )


if __name__ == "__main__":
    unittest.main()
