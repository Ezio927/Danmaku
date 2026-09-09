"""Focused tests for the deterministic Host Super Chat top-presentation lifecycle.

The lifecycle models received -> pending -> displayed -> (expired | deleted) for
canonical ``kind="superChat"`` messages at the core/host boundary, driven by an
injected millisecond clock. The complete message is always retained in canonical
host state; this module only tracks presentation state. Nothing here changes the
protocol-v1 frame grammar or the OBS delivery path.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.hub import DistributionHub  # noqa: E402
from danmaku.core.model import Message  # noqa: E402
from danmaku.core.superchat import (  # noqa: E402
    DELETED,
    DISPLAYED,
    EXPIRED,
    PENDING,
    PRESENTATION_INTERVAL_MILLISECONDS,
    PRESENTATION_INTERVAL_SECONDS,
    SuperChatLifecycle,
)

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


def make_clock(initial_millis):
    """Return ``(state, clock)`` where mutating ``state["now"]`` moves time."""
    state = {"now": initial_millis}

    def clock() -> int:
        return state["now"]

    return state, clock


def make_sc(sequence, received_at=at(0), duration_seconds=60) -> Message:
    return Message.from_dict(
        {
            "id": f"sc:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": received_at,
            "source": "mock",
            "kind": "superChat",
            "user": {"id": "user:carol", "name": "Carol"},
            "data": {
                "text": "hi",
                "amountMilliCny": 30000,
                "durationSeconds": duration_seconds,
            },
        }
    )


def make_danmaku(sequence) -> Message:
    return Message.from_dict(
        {
            "id": f"dm:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": at(0),
            "source": "mock",
            "kind": "danmaku",
            "user": {"id": "user:alice", "name": "Alice"},
            "data": {"text": "hello"},
        }
    )


class SuperChatLifecycleConstantTests(unittest.TestCase):
    def test_presentation_interval_is_three_seconds(self):
        self.assertEqual(PRESENTATION_INTERVAL_SECONDS, 3)
        self.assertEqual(PRESENTATION_INTERVAL_MILLISECONDS, 3000)

    def test_state_constants_are_distinct_strings(self):
        self.assertEqual(
            {PENDING, DISPLAYED, EXPIRED, DELETED},
            {"pending", "displayed", "expired", "deleted"},
        )


class SuperChatLifecycleValidationTests(unittest.TestCase):
    def test_clock_must_be_callable_or_none(self):
        with self.assertRaises(TypeError):
            SuperChatLifecycle(clock=123)
        SuperChatLifecycle(clock=None)

    def test_receive_requires_a_message(self):
        lifecycle = SuperChatLifecycle()
        with self.assertRaises(TypeError):
            lifecycle.receive({"id": "x"})

    def test_receive_requires_a_super_chat(self):
        lifecycle = SuperChatLifecycle()
        with self.assertRaises(TypeError):
            lifecycle.receive(make_danmaku(1))

    def test_receive_rejects_duplicate_id(self):
        lifecycle = SuperChatLifecycle()
        message = make_sc(1)
        lifecycle.receive(message)
        with self.assertRaises(ValueError):
            lifecycle.receive(message)


class SuperChatLifecyclePendingTests(unittest.TestCase):
    def test_receive_enters_pending_and_retains_record(self):
        _, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1))
        self.assertEqual(lifecycle.pending_count, 1)
        self.assertEqual([m.sequence for m in lifecycle.pending()], [1])
        records = lifecycle.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].state, PENDING)
        self.assertEqual(records[0].message.sequence, 1)
        self.assertIsNone(records[0].displayed_at)
        self.assertIsNone(records[0].deleted_at)

    def test_pending_is_fifo_by_receive_order(self):
        _, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0)))
        lifecycle.receive(make_sc(2, at(1000)))
        lifecycle.receive(make_sc(3, at(2000)))
        self.assertEqual(lifecycle.pending_count, 3)
        self.assertEqual(
            [m.sequence for m in lifecycle.pending()], [1, 2, 3]
        )


class SuperChatLifecycleDisplayTests(unittest.TestCase):
    def test_stays_pending_before_three_second_interval(self):
        state, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1))
        state["now"] = BASE_MILLIS + 2999
        self.assertEqual(lifecycle.advance(), ())
        self.assertIsNone(lifecycle.displayed)
        self.assertEqual(lifecycle.pending_count, 1)

    def test_becomes_displayed_at_exactly_three_seconds_inclusive(self):
        state, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1))
        state["now"] = BASE_MILLIS + 3000
        changed = lifecycle.advance()
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].state, DISPLAYED)
        self.assertEqual(changed[0].displayed_at, at(3000))
        self.assertIsNotNone(lifecycle.displayed)
        self.assertEqual(lifecycle.displayed.sequence, 1)
        self.assertEqual(lifecycle.pending_count, 0)

    def test_earliest_super_chat_is_selected_deterministically(self):
        state, clock = make_clock(BASE_MILLIS + 10000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0)))
        lifecycle.receive(make_sc(2, at(1000)))
        lifecycle.receive(make_sc(3, at(2000)))
        changed = lifecycle.advance()
        self.assertEqual([r.message.sequence for r in changed], [1])
        self.assertEqual(lifecycle.displayed.sequence, 1)
        self.assertEqual(lifecycle.pending_count, 2)
        self.assertEqual(
            [m.sequence for m in lifecycle.pending()], [2, 3]
        )

    def test_single_top_slot_holds_later_pending_back(self):
        state, clock = make_clock(BASE_MILLIS + 10000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0), duration_seconds=60))
        lifecycle.receive(make_sc(2, at(1000), duration_seconds=60))
        lifecycle.advance()
        # Even though both are due, only one top presentation exists.
        self.assertEqual(lifecycle.displayed.sequence, 1)
        self.assertEqual(lifecycle.pending_count, 1)


class SuperChatLifecycleExpiryTests(unittest.TestCase):
    def test_expiry_removes_top_and_promotes_next(self):
        state, clock = make_clock(BASE_MILLIS + 3000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0), duration_seconds=60))
        lifecycle.receive(make_sc(2, at(1000), duration_seconds=60))
        lifecycle.advance()
        self.assertEqual(lifecycle.displayed.sequence, 1)

        # Sequence 1 expires at BASE + 3000 + 60000 = BASE + 63000.
        state["now"] = BASE_MILLIS + 62999
        self.assertEqual(lifecycle.advance(), ())
        self.assertEqual(lifecycle.displayed.sequence, 1)

        state["now"] = BASE_MILLIS + 63000
        changed = lifecycle.advance()
        self.assertEqual(
            [(r.message.sequence, r.state) for r in changed],
            [(1, EXPIRED), (2, DISPLAYED)],
        )
        self.assertEqual(lifecycle.displayed.sequence, 2)

    def test_expiry_retains_complete_timeline_item(self):
        state, clock = make_clock(BASE_MILLIS + 3000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0), duration_seconds=60))
        lifecycle.advance()
        state["now"] = BASE_MILLIS + 63000
        lifecycle.advance()
        record = lifecycle.record("sc:0001")
        self.assertIsNotNone(record)
        self.assertEqual(record.state, EXPIRED)
        self.assertEqual(record.message.sequence, 1)
        self.assertEqual(record.message.data["durationSeconds"], 60)
        self.assertEqual(record.expired_at, at(63000))
        self.assertIsNone(record.deleted_at)

    def test_records_preserve_receive_order_across_states(self):
        state, clock = make_clock(BASE_MILLIS + 63000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0), duration_seconds=60))
        lifecycle.receive(make_sc(2, at(1000), duration_seconds=60))
        lifecycle.advance()
        records = lifecycle.records()
        self.assertEqual(
            [(r.message.sequence, r.state) for r in records],
            [(1, EXPIRED), (2, DISPLAYED)],
        )


class SuperChatLifecycleDeleteTests(unittest.TestCase):
    def test_delete_removes_displayed_top_and_marks_record(self):
        state, clock = make_clock(BASE_MILLIS + 3000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1))
        lifecycle.advance()
        self.assertIsNotNone(lifecycle.displayed)

        state["now"] = BASE_MILLIS + 5000
        record = lifecycle.delete("sc:0001")
        self.assertIsNotNone(record)
        self.assertEqual(record.state, DELETED)
        self.assertEqual(record.deleted_at, at(5000))
        self.assertIsNone(lifecycle.displayed)
        self.assertEqual(lifecycle.pending_count, 0)
        self.assertEqual(lifecycle.record("sc:0001").state, DELETED)

    def test_delete_removes_pending_from_queue(self):
        state, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0)))
        lifecycle.receive(make_sc(2, at(1000)))
        state["now"] = BASE_MILLIS + 500
        record = lifecycle.delete("sc:0002")
        self.assertEqual(record.state, DELETED)
        self.assertIsNotNone(record.deleted_at)
        self.assertEqual(lifecycle.pending_count, 1)
        self.assertEqual([m.sequence for m in lifecycle.pending()], [1])

    def test_deleted_record_is_retained_with_context(self):
        state, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0)))
        state["now"] = BASE_MILLIS + 500
        lifecycle.delete("sc:0001")
        records = lifecycle.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].state, DELETED)
        self.assertEqual(records[0].message.sequence, 1)
        self.assertEqual(records[0].deleted_at, at(500))

    def test_delete_unknown_id_raises(self):
        _, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        with self.assertRaises(KeyError):
            lifecycle.delete("unknown")

    def test_delete_expired_is_noop(self):
        state, clock = make_clock(BASE_MILLIS + 63000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0), duration_seconds=60))
        lifecycle.advance()
        self.assertEqual(lifecycle.record("sc:0001").state, EXPIRED)
        self.assertIsNone(lifecycle.delete("sc:0001"))
        self.assertEqual(lifecycle.record("sc:0001").state, EXPIRED)

    def test_delete_is_idempotent(self):
        state, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1))
        first = lifecycle.delete("sc:0001")
        state["now"] = BASE_MILLIS + 500
        second = lifecycle.delete("sc:0001")
        self.assertEqual(first.state, DELETED)
        self.assertEqual(second.state, DELETED)
        self.assertEqual(first.deleted_at, at(0))
        self.assertEqual(second.deleted_at, at(0))


class SuperChatLifecycleManualTests(unittest.TestCase):
    def test_select_reorders_pending_to_front(self):
        state, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0)))
        lifecycle.receive(make_sc(2, at(1000)))
        lifecycle.receive(make_sc(3, at(2000)))
        lifecycle.select("sc:0003")
        self.assertEqual(
            [m.sequence for m in lifecycle.pending()], [3, 1, 2]
        )
        state["now"] = BASE_MILLIS + 10000
        changed = lifecycle.advance()
        self.assertEqual(changed[0].message.sequence, 3)
        self.assertEqual(lifecycle.displayed.sequence, 3)

    def test_select_rejects_non_pending_or_unknown(self):
        state, clock = make_clock(BASE_MILLIS + 3000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1))
        lifecycle.advance()
        with self.assertRaises(ValueError):
            lifecycle.select("sc:0001")
        with self.assertRaises(KeyError):
            lifecycle.select("unknown")

    def test_skip_advances_top_immediately(self):
        state, clock = make_clock(BASE_MILLIS + 3000)
        lifecycle = SuperChatLifecycle(clock=clock)
        lifecycle.receive(make_sc(1, at(0), duration_seconds=60))
        lifecycle.receive(make_sc(2, at(1000), duration_seconds=60))
        lifecycle.advance()
        self.assertEqual(lifecycle.displayed.sequence, 1)

        skipped = lifecycle.skip()
        self.assertIsNotNone(skipped)
        self.assertEqual(skipped.state, EXPIRED)
        self.assertEqual(skipped.message.sequence, 1)
        self.assertEqual(skipped.expired_at, at(3000))
        self.assertEqual(lifecycle.displayed.sequence, 2)
        self.assertEqual(lifecycle.pending_count, 0)

    def test_skip_with_no_top_returns_none(self):
        _, clock = make_clock(BASE_MILLIS)
        lifecycle = SuperChatLifecycle(clock=clock)
        self.assertIsNone(lifecycle.skip())


class DistributionHubSuperChatCompositionTests(unittest.TestCase):
    def test_publish_feeds_super_chat_into_lifecycle_and_host_state(self):
        hub = DistributionHub(clock=lambda: BASE_MILLIS)
        hub.publish(make_sc(1))
        self.assertEqual(hub.superchat.pending_count, 1)
        self.assertEqual([m.sequence for m in hub.snapshot()], [1])

    def test_publish_ignores_non_super_chat(self):
        hub = DistributionHub(clock=lambda: BASE_MILLIS)
        hub.publish(make_danmaku(1))
        self.assertEqual(hub.superchat.pending_count, 0)
        self.assertEqual(hub.superchat.records(), ())
        self.assertEqual([m.sequence for m in hub.snapshot()], [1])

    def test_hub_lifecycle_shares_injected_clock(self):
        state, clock = make_clock(BASE_MILLIS)
        hub = DistributionHub(clock=clock)
        hub.publish(make_sc(1))
        state["now"] = BASE_MILLIS + 3000
        changed = hub.superchat.advance()
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].state, DISPLAYED)

    def test_hub_uses_injected_lifecycle(self):
        lifecycle = SuperChatLifecycle(clock=lambda: BASE_MILLIS)
        hub = DistributionHub(clock=lambda: BASE_MILLIS, superchat=lifecycle)
        self.assertIs(hub.superchat, lifecycle)
        hub.publish(make_sc(1))
        self.assertEqual(lifecycle.pending_count, 1)

    def test_hub_rejects_non_lifecycle_superchat(self):
        with self.assertRaises(TypeError):
            DistributionHub(superchat=object())


if __name__ == "__main__":
    unittest.main()
