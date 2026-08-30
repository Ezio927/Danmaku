"""Public-behavior tests for the deterministic mock source."""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.mock.source import MockSource  # noqa: E402

FIXTURES = ROOT / "docs" / "protocol-fixtures"


def _fixed_clock():
    return datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


class MockSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = MockSource(cadence=1.0, start_sequence=1, clock=_fixed_clock)

    def test_first_four_messages_match_snapshot_fixture(self):
        snapshot = json.loads((FIXTURES / "snapshot.json").read_text(encoding="utf-8"))
        expected = snapshot["payload"]["messages"]
        self.assertEqual(len(expected), 4)
        for sequence, expected_message in enumerate(expected, start=1):
            with self.subTest(sequence=sequence):
                self.assertEqual(
                    self.source.build_message(sequence).to_dict(), expected_message
                )

    def test_deterministic_across_instances(self):
        first = MockSource(cadence=1.0, start_sequence=1, clock=_fixed_clock)
        second = MockSource(cadence=1.0, start_sequence=1, clock=_fixed_clock)
        for sequence in range(1, 9):
            self.assertEqual(
                first.build_message(sequence).to_dict(),
                second.build_message(sequence).to_dict(),
            )

    def test_cycles_four_approved_kinds_in_order(self):
        kinds = [self.source.build_message(i).kind for i in range(1, 9)]
        self.assertEqual(
            kinds,
            [
                "danmaku",
                "gift",
                "guard",
                "superChat",
                "danmaku",
                "gift",
                "guard",
                "superChat",
            ],
        )

    def test_sequence_is_monotonic_and_zero_padded_id(self):
        self.assertEqual(self.source.build_message(1).id, "mock:0001")
        self.assertEqual(self.source.build_message(2).id, "mock:0002")
        sequences = [self.source.build_message(i).sequence for i in range(1, 6)]
        self.assertEqual(sequences, [1, 2, 3, 4, 5])

    def test_received_at_advances_by_cadence(self):
        self.assertEqual(
            self.source.build_message(1).received_at, "2026-01-01T00:00:00.000Z"
        )
        self.assertEqual(
            self.source.build_message(2).received_at, "2026-01-01T00:00:01.000Z"
        )
        self.assertEqual(
            self.source.build_message(4).received_at, "2026-01-01T00:00:03.000Z"
        )

    def test_start_sequence_seed_shifts_output(self):
        source = MockSource(cadence=1.0, start_sequence=5, clock=_fixed_clock)
        message = source.build_message(5)
        self.assertEqual(message.sequence, 5)
        self.assertEqual(message.id, "mock:0005")
        self.assertEqual(message.kind, "danmaku")

    def test_build_message_rejects_sequence_below_seed(self):
        with self.assertRaises(ValueError):
            self.source.build_message(0)

    def test_invalid_cadence_rejected(self):
        with self.assertRaises(ValueError):
            MockSource(cadence=-1.0)

    def test_invalid_start_sequence_rejected(self):
        with self.assertRaises(ValueError):
            MockSource(start_sequence=0)


class MockSourceAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_iteration_emits_in_order(self):
        source = MockSource(cadence=0, start_sequence=1, clock=_fixed_clock)
        sequences = []
        async for message in source:
            sequences.append(message.sequence)
            if len(sequences) == 4:
                break
        self.assertEqual(sequences, [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
