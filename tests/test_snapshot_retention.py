"""Focused tests for the five-minute OBS snapshot retention policy.

The OBS delivery snapshot (:meth:`DistributionHub.filtered_snapshot`) must
contain only messages received within the fixed five-minute retention window
of the injected clock and at most 100 messages, while the canonical host state
(:meth:`DistributionHub.snapshot`) and live subscriber delivery stay complete
and unchanged. The five-minute boundary is inclusive and the clock is a
deterministic, injectable timestamp seam.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.hub import (  # noqa: E402
    SNAPSHOT_RETENTION_MILLISECONDS,
    DistributionHub,
)
from danmaku.core.model import Message  # noqa: E402
from danmaku.core.snapshot import SnapshotStore  # noqa: E402

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _millis(value: str) -> int:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    delta = parsed - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def at(offset_millis: int) -> str:
    """RFC 3339 UTC timestamp ``offset_millis`` after the fixed base instant."""
    value = _BASE + timedelta(milliseconds=offset_millis)
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}Z"


BASE_MILLIS = _millis(at(0))


def make_message(sequence: int, received_at: str = at(0)) -> Message:
    return Message.from_dict(
        {
            "id": f"ret:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": received_at,
            "source": "mock",
            "kind": "danmaku",
            "user": {"id": "user:alice", "name": "Alice"},
            "data": {"text": "hello"},
        }
    )


class RetentionConstantTests(unittest.TestCase):
    def test_retention_window_is_exactly_five_minutes(self):
        self.assertEqual(SNAPSHOT_RETENTION_MILLISECONDS, 300_000)


class RetentionValidationTests(unittest.TestCase):
    def test_retention_window_must_be_a_non_negative_integer(self):
        for bad in (-1, True, 1.0, "300000", None):
            with self.subTest(retention=bad):
                with self.assertRaises(ValueError):
                    DistributionHub(snapshot_retention_milliseconds=bad)
        DistributionHub(snapshot_retention_milliseconds=0)

    def test_clock_must_be_callable_or_none(self):
        with self.assertRaises(TypeError):
            DistributionHub(clock=123)


class SnapshotRetentionTests(unittest.IsolatedAsyncioTestCase):
    def _hub(self, now_offset: int) -> DistributionHub:
        return DistributionHub(clock=lambda: BASE_MILLIS + now_offset)

    async def test_exact_five_minute_boundary_included_and_older_excluded(self):
        hub = self._hub(300_000)
        hub.publish(make_message(1, at(-1)))  # 300001 ms old -> excluded
        hub.publish(make_message(2, at(0)))  # 300000 ms old -> included
        hub.publish(make_message(3, at(1)))  # 299999 ms old -> included
        self.assertEqual([m.sequence for m in hub.filtered_snapshot()], [2, 3])

    async def test_snapshot_retains_no_more_than_100_delivered_messages(self):
        hub = DistributionHub(clock=lambda: BASE_MILLIS + 200_000)
        for sequence in range(1, 121):
            hub.publish(make_message(sequence, at(sequence * 1000)))
        snapshot = hub.filtered_snapshot()
        self.assertEqual(len(snapshot), 100)
        self.assertEqual(snapshot[0].sequence, 21)
        self.assertEqual(snapshot[-1].sequence, 120)

    async def test_canonical_snapshot_is_not_trimmed_by_age(self):
        hub = self._hub(300_000)
        hub.publish(make_message(1, at(-1)))
        hub.publish(make_message(2, at(0)))
        self.assertEqual([m.sequence for m in hub.filtered_snapshot()], [2])
        self.assertEqual([m.sequence for m in hub.snapshot()], [1, 2])

    async def test_canonical_store_object_is_not_filtered_by_age(self):
        store = SnapshotStore(max_messages=100)
        hub = DistributionHub(store=store, clock=lambda: BASE_MILLIS + 300_000)
        hub.publish(make_message(1, at(-1)))
        hub.publish(make_message(2, at(0)))
        self.assertEqual([m.sequence for m in store.list()], [1, 2])
        self.assertEqual([m.sequence for m in hub.filtered_snapshot()], [2])

    async def test_live_delivery_is_not_affected_by_age(self):
        hub = self._hub(300_000)
        subscriber = hub.subscribe()
        hub.publish(make_message(1, at(-1)))
        hub.publish(make_message(2, at(0)))
        received = [(await subscriber.receive()).sequence for _ in range(2)]
        self.assertEqual(received, [1, 2])

    async def test_retention_is_applied_at_read_time(self):
        now = [BASE_MILLIS + 300_000]
        hub = DistributionHub(clock=lambda: now[0])
        hub.publish(make_message(1, at(0)))  # exactly 300000 ms old
        self.assertEqual([m.sequence for m in hub.filtered_snapshot()], [1])
        now[0] = BASE_MILLIS + 300_001  # one ms later the message is stale
        self.assertEqual(hub.filtered_snapshot(), ())

    async def test_oldest_first_order_is_preserved(self):
        hub = self._hub(300_000)
        hub.publish(make_message(1, at(1)))
        hub.publish(make_message(2, at(2)))
        hub.publish(make_message(3, at(3)))
        self.assertEqual([m.sequence for m in hub.filtered_snapshot()], [1, 2, 3])

    async def test_independent_subscribers_and_shared_snapshot(self):
        hub = self._hub(300_000)
        first = hub.subscribe()
        second = hub.subscribe()
        hub.publish(make_message(1, at(-1)))
        hub.publish(make_message(2, at(0)))
        # Both subscribers receive both messages regardless of age.
        self.assertEqual(
            [(await first.receive()).sequence for _ in range(2)], [1, 2]
        )
        self.assertEqual(
            [(await second.receive()).sequence for _ in range(2)], [1, 2]
        )
        # The shared filtered snapshot is age-trimmed while the queues stay
        # intact and independent.
        self.assertEqual([m.sequence for m in hub.filtered_snapshot()], [2])


if __name__ == "__main__":
    unittest.main()
