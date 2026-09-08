"""Public-behavior tests for the canonical message model."""

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.model import Message, User, ValidationError  # noqa: E402

FIXTURES = ROOT / "docs" / "protocol-fixtures"

MAX_SEQUENCE = 9_007_199_254_740_991


def _load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FixtureRoundTripTests(unittest.TestCase):
    def test_each_message_created_fixture_round_trips(self):
        for name in (
            "message-created-danmaku.json",
            "message-created-gift.json",
            "message-created-guard.json",
            "message-created-super-chat.json",
        ):
            with self.subTest(fixture=name):
                frame = _load_json(name)
                message = frame["payload"]["message"]
                parsed = Message.from_dict(message)
                self.assertEqual(parsed.to_dict(), message)

    def test_snapshot_fixture_round_trips_all_four_messages(self):
        snapshot = _load_json("snapshot.json")
        messages = snapshot["payload"]["messages"]
        self.assertEqual(len(messages), 4)
        for message in messages:
            parsed = Message.from_dict(message)
            self.assertEqual(parsed.to_dict(), message)

    def test_parse_and_serialize_round_trip(self):
        frame = _load_json("message-created-danmaku.json")
        text = json.dumps(frame["payload"]["message"])
        parsed = Message.parse(text)
        self.assertEqual(json.loads(parsed.to_json()), frame["payload"]["message"])


class ExactKeyTests(unittest.TestCase):
    def _valid(self):
        return {
            "id": "mock:0001",
            "sequence": 1,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": "danmaku",
            "user": {"id": "user:alice", "name": "Alice"},
            "data": {"text": "hello"},
        }

    def test_unknown_top_level_key_rejected(self):
        value = self._valid()
        value["extra"] = True
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_missing_top_level_key_rejected(self):
        value = self._valid()
        del value["sequence"]
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_unknown_user_key_rejected(self):
        value = self._valid()
        value["user"]["avatar"] = "x"
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_unknown_data_key_rejected(self):
        value = self._valid()
        value["data"]["extra"] = "x"
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_missing_data_key_rejected(self):
        value = self._valid()
        del value["data"]["text"]
        with self.assertRaises(ValidationError):
            Message.from_dict(value)


class ScalarValidationTests(unittest.TestCase):
    def _valid(self, kind="danmaku"):
        base = {
            "id": "mock:0001",
            "sequence": 1,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": "user:alice", "name": "Alice"},
        }
        data = {
            "danmaku": {"text": "hello"},
            "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
            "guard": {"tier": "captain", "months": 1},
            "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
        }[kind]
        return {**base, "data": data}

    def test_boolean_rejected_as_integer(self):
        for field in ("sequence",):
            value = self._valid()
            value[field] = True
            with self.assertRaises(ValidationError):
                Message.from_dict(value)

    def test_boolean_rejected_in_kind_data(self):
        value = self._valid("gift")
        value["data"]["quantity"] = True
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_float_rejected_as_integer(self):
        value = self._valid()
        value["sequence"] = 1.0
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_sequence_zero_rejected(self):
        value = self._valid()
        value["sequence"] = 0
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_sequence_negative_rejected(self):
        value = self._valid()
        value["sequence"] = -1
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_sequence_max_accepted(self):
        value = self._valid()
        value["sequence"] = MAX_SEQUENCE
        self.assertEqual(Message.from_dict(value).sequence, MAX_SEQUENCE)

    def test_sequence_above_max_rejected(self):
        value = self._valid()
        value["sequence"] = MAX_SEQUENCE + 1
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_non_finite_number_rejected_at_parse(self):
        text = (
            '{"id":"mock:1","sequence":NaN,"receivedAt":'
            '"2026-01-01T00:00:00.000Z","source":"mock","kind":"danmaku",'
            '"user":{"id":"user:a","name":"A"},"data":{"text":"hi"}}'
        )
        with self.assertRaises(ValidationError):
            Message.parse(text)

    def test_non_finite_float_rejected_in_dict(self):
        value = self._valid()
        value["sequence"] = math.nan
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_source_must_be_mock(self):
        value = self._valid()
        value["source"] = "bilibili"
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_unknown_kind_rejected(self):
        value = self._valid()
        value["kind"] = "superchat"
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_invalid_json_rejected(self):
        with self.assertRaises(ValidationError):
            Message.parse("{not json")

    def test_parse_requires_string(self):
        with self.assertRaises(ValidationError):
            Message.parse({"id": "mock:1"})

    def test_id_must_start_alphanumeric(self):
        value = self._valid()
        value["id"] = "_mock"
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_id_too_long_rejected(self):
        value = self._valid()
        value["id"] = "a" * 65
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_id_allowed_characters(self):
        value = self._valid()
        value["id"] = "A0._:-z"
        self.assertEqual(Message.from_dict(value).id, "A0._:-z")

    def test_received_at_invalid_formats_rejected(self):
        bad = (
            "2026-01-01 00:00:00.000Z",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00.00Z",
            "2026-01-01T00:00:00.0000Z",
            "2026-13-01T00:00:00.000Z",
        )
        for received_at in bad:
            with self.subTest(received_at=received_at):
                value = self._valid()
                value["receivedAt"] = received_at
                with self.assertRaises(ValidationError):
                    Message.from_dict(value)

    def test_received_at_valid_accepted(self):
        value = self._valid()
        self.assertEqual(
            Message.from_dict(value).received_at, "2026-01-01T00:00:00.000Z"
        )


class StringRuleTests(unittest.TestCase):
    def _valid(self):
        return {
            "id": "mock:0001",
            "sequence": 1,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": "danmaku",
            "user": {"id": "user:alice", "name": "Alice"},
            "data": {"text": "hello"},
        }

    def test_control_character_in_name_rejected(self):
        value = self._valid()
        value["user"]["name"] = "Ali\u0000ce"
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_control_character_in_text_rejected(self):
        value = self._valid()
        value["data"]["text"] = "hi\nthere"
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_astral_character_counts_as_one_code_point(self):
        value = self._valid()
        value["data"]["text"] = "\U0001f600" * 500
        self.assertEqual(
            len(Message.from_dict(value).to_dict()["data"]["text"]), 500
        )

    def test_user_name_one_to_sixty_four(self):
        value = self._valid()
        value["user"]["name"] = "x"
        Message.from_dict(value)
        value["user"]["name"] = "x" * 64
        Message.from_dict(value)
        value["user"]["name"] = "x" * 65
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_danmaku_text_bounds(self):
        value = self._valid()
        value["data"]["text"] = "x" * 500
        Message.from_dict(value)
        value["data"]["text"] = "x" * 501
        with self.assertRaises(ValidationError):
            Message.from_dict(value)


class KindDataTests(unittest.TestCase):
    def _valid(self, kind):
        return {
            "id": "mock:0001",
            "sequence": 1,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": "user:alice", "name": "Alice"},
            "data": self._data(kind),
        }

    def _data(self, kind):
        return {
            "danmaku": {"text": "hello"},
            "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
            "guard": {"tier": "captain", "months": 1},
            "superChat": {"text": "hi", "amountMilliCny": 30000, "durationSeconds": 60},
        }[kind]

    def test_gift_quantity_bounds(self):
        value = self._valid("gift")
        value["data"]["quantity"] = 0
        with self.assertRaises(ValidationError):
            Message.from_dict(value)
        value["data"]["quantity"] = 1_000_001
        with self.assertRaises(ValidationError):
            Message.from_dict(value)
        value["data"]["quantity"] = 1_000_000
        Message.from_dict(value)

    def test_gift_amount_bounds(self):
        value = self._valid("gift")
        value["data"]["totalAmountMilliCny"] = -1
        with self.assertRaises(ValidationError):
            Message.from_dict(value)
        value["data"]["totalAmountMilliCny"] = MAX_SEQUENCE
        Message.from_dict(value)

    def test_gift_name_bounds(self):
        value = self._valid("gift")
        value["data"]["giftName"] = "x" * 100
        Message.from_dict(value)
        value["data"]["giftName"] = "x" * 101
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_guard_tier_rejected(self):
        value = self._valid("guard")
        value["data"]["tier"] = "emperor"
        with self.assertRaises(ValidationError):
            Message.from_dict(value)

    def test_guard_months_bounds(self):
        value = self._valid("guard")
        value["data"]["months"] = 0
        with self.assertRaises(ValidationError):
            Message.from_dict(value)
        value["data"]["months"] = 121
        with self.assertRaises(ValidationError):
            Message.from_dict(value)
        value["data"]["months"] = 120
        Message.from_dict(value)

    def test_super_chat_bounds(self):
        value = self._valid("superChat")
        value["data"]["amountMilliCny"] = 0
        with self.assertRaises(ValidationError):
            Message.from_dict(value)
        value["data"]["amountMilliCny"] = MAX_SEQUENCE
        value["data"]["durationSeconds"] = 0
        with self.assertRaises(ValidationError):
            Message.from_dict(value)
        value["data"]["durationSeconds"] = 86_401
        with self.assertRaises(ValidationError):
            Message.from_dict(value)
        value["data"]["durationSeconds"] = 86_400
        Message.from_dict(value)


class ImmutabilityTests(unittest.TestCase):
    def test_message_is_immutable(self):
        value = {
            "id": "mock:0001",
            "sequence": 1,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": "danmaku",
            "user": {"id": "user:alice", "name": "Alice"},
            "data": {"text": "hello"},
        }
        message = Message.from_dict(value)
        with self.assertRaises(AttributeError):
            message.sequence = 2
        self.assertIsInstance(message.user, User)


class CanonicalConstructionTests(unittest.TestCase):
    def _message(self, **overrides):
        fields = {
            "id": "mock:0001",
            "sequence": 1,
            "received_at": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": "danmaku",
            "user": User(id="user:alice", name="Alice"),
            "data": {"text": "hello"},
        }
        fields.update(overrides)
        return Message(**fields)

    def test_user_direct_construction_rejects_invalid_id(self):
        with self.assertRaises(ValidationError):
            User(id="_bad", name="Alice")

    def test_user_direct_construction_rejects_empty_name(self):
        with self.assertRaises(ValidationError):
            User(id="user:a", name="")

    def test_user_direct_construction_rejects_control_character(self):
        with self.assertRaises(ValidationError):
            User(id="user:a", name="Ali\u0000ce")

    def test_user_direct_construction_accepts_canonical(self):
        user = User(id="user:alice", name="Alice")
        self.assertEqual(user.to_dict(), {"id": "user:alice", "name": "Alice"})

    def test_message_direct_construction_rejects_invalid_id(self):
        with self.assertRaises(ValidationError):
            self._message(id="_bad")

    def test_message_direct_construction_rejects_invalid_sequence(self):
        with self.assertRaises(ValidationError):
            self._message(sequence=0)

    def test_message_direct_construction_rejects_invalid_kind(self):
        with self.assertRaises(ValidationError):
            self._message(kind="superchat")

    def test_message_direct_construction_rejects_non_mock_source(self):
        with self.assertRaises(ValidationError):
            self._message(source="bilibili")

    def test_message_direct_construction_rejects_invalid_received_at(self):
        with self.assertRaises(ValidationError):
            self._message(received_at="2026-01-01T00:00:00Z")

    def test_message_direct_construction_rejects_invalid_data(self):
        with self.assertRaises(ValidationError):
            self._message(data={"text": ""})

    def test_message_direct_construction_accepts_user_dict(self):
        message = self._message(user={"id": "user:alice", "name": "Alice"})
        self.assertIsInstance(message.user, User)

    def test_message_direct_construction_rejects_unknown_user_key(self):
        with self.assertRaises(ValidationError):
            self._message(user={"id": "user:alice", "name": "Alice", "avatar": "x"})

    def test_message_direct_construction_round_trips(self):
        message = self._message()
        self.assertEqual(
            message.to_dict(),
            {
                "id": "mock:0001",
                "sequence": 1,
                "receivedAt": "2026-01-01T00:00:00.000Z",
                "source": "mock",
                "kind": "danmaku",
                "user": {"id": "user:alice", "name": "Alice"},
                "data": {"text": "hello"},
            },
        )

    def test_from_dict_still_validates_nested_user(self):
        value = {
            "id": "mock:0001",
            "sequence": 1,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": "danmaku",
            "user": {"id": "user:alice", "name": ""},
            "data": {"text": "hello"},
        }
        with self.assertRaises(ValidationError):
            Message.from_dict(value)


if __name__ == "__main__":
    unittest.main()
