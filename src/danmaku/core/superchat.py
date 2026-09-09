"""Deterministic Super Chat top-presentation lifecycle.

A received Super Chat is a canonical ``kind == "superChat"`` message. The
canonical host timeline (``SnapshotStore`` / ``DistributionHub.snapshot()``)
always retains the complete message unchanged; this module models only the
*top-presentation* lifecycle the host surface derives from it, independently of
the OBS delivery path and without any protocol-v1 change.

The lifecycle is a pure, transport-independent state machine over four states:

* ``pending``  — received and retained in canonical host state, waiting for its
  presentation turn;
* ``displayed`` — the single active top presentation (the pinned card);
* ``expired``  — its presentation duration elapsed and it left the top slot,
  while its timeline item stays retained;
* ``deleted``  — manually removed from the active top/card presentation, with
  its timeline item retained and clearly marked deleted with a ``deletedAt``
  instant.

Transitions are deterministic and driven by an injected millisecond clock
(``clock``), so the exact time boundaries are testable:

* A received Super Chat enters ``pending`` in deterministic receive order
  (which the canonical store guarantees is strictly increasing sequence order).
* The earliest not-yet-displayed Super Chat (the front of the pending queue) is
  the single candidate for the top slot. It becomes ``displayed`` once the
  documented :data:`PRESENTATION_INTERVAL_SECONDS` (three seconds) have elapsed
  since its ``receivedAt``. The boundary is inclusive: exactly three seconds
  after receipt it is displayed.
* The ``displayed`` Super Chat is the only top presentation. It expires
  ``data.durationSeconds`` after it became displayed. Expiry removes only the
  top presentation; the message stays in the canonical host timeline.
* Manual operations are public seams: :meth:`SuperChatLifecycle.select`
  reorders a pending Super Chat to the front of the queue (manual selection),
  :meth:`SuperChatLifecycle.skip` advances the top presentation immediately,
  and :meth:`SuperChatLifecycle.delete` removes a pending or displayed Super
  Chat from presentation and marks its retained timeline record deleted with
  the deletion instant.

None of this changes the frozen protocol-v1 frame grammar, the OBS delivery
path, or the canonical message model. The lifecycle is fed by
:meth:`DistributionHub.publish` and advanced explicitly by its consumer.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .model import Message

__all__ = [
    "DELETED",
    "DISPLAYED",
    "EXPIRED",
    "PENDING",
    "PRESENTATION_INTERVAL_MILLISECONDS",
    "PRESENTATION_INTERVAL_SECONDS",
    "SuperChatLifecycle",
    "SuperChatRecord",
]

#: Lifecycle states. ``pending`` waits, ``displayed`` is the single active top
#: presentation, ``expired`` left the top slot on its own, and ``deleted`` was
#: manually removed from presentation with its timeline item retained.
PENDING = "pending"
DISPLAYED = "displayed"
EXPIRED = "expired"
DELETED = "deleted"

#: A received Super Chat waits this many seconds in ``pending`` before it
#: becomes the top ``displayed`` presentation. Documented in
#: ``docs/super-chat-lifecycle.md``.
PRESENTATION_INTERVAL_SECONDS = 3
PRESENTATION_INTERVAL_MILLISECONDS = PRESENTATION_INTERVAL_SECONDS * 1000

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _received_at_millis(value: str) -> int:
    """Return the exact integer millisecond offset of an RFC 3339 timestamp."""
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    delta = parsed - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def _format_millis(millis: int) -> str:
    """Return an RFC 3339 UTC timestamp for an integer millisecond instant."""
    seconds, milli = divmod(millis, 1000)
    value = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{milli:03d}Z"


def _default_clock() -> int:
    """Return the current UTC instant in integer milliseconds since the epoch."""
    return time.time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class SuperChatRecord:
    """An immutable snapshot of one Super Chat's presentation state.

    ``message`` is the unchanged canonical timeline item. ``state`` is one of
    ``pending``, ``displayed``, ``expired``, or ``deleted``. The timestamp
    fields are set exactly when their transition happened and remain ``None``
    otherwise; a ``deleted`` record always carries ``deleted_at`` so the
    retained timeline item is clearly marked with the deletion context.
    """

    message: Message
    state: str
    displayed_at: str | None = None
    expired_at: str | None = None
    deleted_at: str | None = None


class _Entry:
    """Mutable internal per-message lifecycle state."""

    __slots__ = (
        "message",
        "state",
        "displayed_at_ms",
        "expired_at_ms",
        "deleted_at_ms",
    )

    def __init__(self, message: Message) -> None:
        self.message = message
        self.state = PENDING
        self.displayed_at_ms: int | None = None
        self.expired_at_ms: int | None = None
        self.deleted_at_ms: int | None = None

    def snapshot(self) -> SuperChatRecord:
        return SuperChatRecord(
            message=self.message,
            state=self.state,
            displayed_at=(
                _format_millis(self.displayed_at_ms)
                if self.displayed_at_ms is not None
                else None
            ),
            expired_at=(
                _format_millis(self.expired_at_ms)
                if self.expired_at_ms is not None
                else None
            ),
            deleted_at=(
                _format_millis(self.deleted_at_ms)
                if self.deleted_at_ms is not None
                else None
            ),
        )


class SuperChatLifecycle:
    """Model the received/displayed/expired/deleted Super Chat top presentation.

    The lifecycle is synchronous and deterministic. It is fed by
    :meth:`receive` (called by ``DistributionHub.publish`` for every accepted
    super-chat message) and advanced by :meth:`advance` using the injected
    clock. ``clock`` is an optional zero-argument callable returning the current
    UTC instant in integer milliseconds since the epoch, mirroring the hub's
    clock seam.
    """

    def __init__(self, *, clock: Callable[[], int] | None = None) -> None:
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable or None")
        self._clock = clock if clock is not None else _default_clock
        self._pending: deque[str] = deque()
        self._entries: dict[str, _Entry] = {}
        self._top_id: str | None = None

    @property
    def pending_count(self) -> int:
        """The number of Super Chats still waiting for their presentation turn."""
        return len(self._pending)

    def pending(self) -> tuple[Message, ...]:
        """Return the pending Super Chats in deterministic receive order."""
        return tuple(self._entries[message_id].message for message_id in self._pending)

    @property
    def displayed(self) -> Message | None:
        """Return the single active top presentation, or ``None`` when empty."""
        if self._top_id is None:
            return None
        return self._entries[self._top_id].message

    def record(self, message_id: str) -> SuperChatRecord | None:
        """Return the immutable lifecycle record for ``message_id``, if known."""
        entry = self._entries.get(message_id)
        return entry.snapshot() if entry is not None else None

    def records(self) -> tuple[SuperChatRecord, ...]:
        """Return every retained lifecycle record in receive order.

        This is the complete lifecycle timeline: pending, displayed, expired,
        and clearly marked deleted records are all retained with their message
        and transition instants.
        """
        return tuple(entry.snapshot() for entry in self._entries.values())

    def receive(self, message: Message) -> None:
        """Admit a super-chat ``message`` into the ``pending`` lifecycle state."""
        if not isinstance(message, Message):
            raise TypeError("receive requires a Message")
        if message.kind != "superChat":
            raise TypeError("receive requires a superChat message")
        if message.id in self._entries:
            raise ValueError(f"duplicate super chat id: {message.id!r}")
        self._entries[message.id] = _Entry(message)
        self._pending.append(message.id)

    def advance(self) -> tuple[SuperChatRecord, ...]:
        """Progress the lifecycle to the injected clock's current instant.

        Deterministically expires any due top presentation and promotes the
        earliest due pending Super Chat until no further transition is due,
        returning the changed records in the order they transitioned. Calling
        with an unmoved clock is a no-op returning an empty tuple.
        """
        now = self._clock()
        changed: list[SuperChatRecord] = []
        while True:
            progressed = False

            if self._top_id is not None:
                top = self._entries[self._top_id]
                expiry_ms = top.displayed_at_ms + (
                    top.message.data["durationSeconds"] * 1000
                )
                if expiry_ms <= now:
                    top.state = EXPIRED
                    top.expired_at_ms = expiry_ms
                    self._top_id = None
                    changed.append(top.snapshot())
                    progressed = True
                    continue

            if self._top_id is None and self._pending:
                front_id = self._pending[0]
                front = self._entries[front_id]
                due_ms = (
                    _received_at_millis(front.message.received_at)
                    + PRESENTATION_INTERVAL_MILLISECONDS
                )
                if due_ms <= now:
                    front.state = DISPLAYED
                    front.displayed_at_ms = due_ms
                    self._pending.popleft()
                    self._top_id = front_id
                    changed.append(front.snapshot())
                    progressed = True
                    continue

            if not progressed:
                break
        return tuple(changed)

    def select(self, message_id: str) -> None:
        """Manually move a pending Super Chat to the front of the queue.

        The named Super Chat becomes the next one ``advance`` displays. It must
        currently be ``pending``; a displayed, expired, or deleted record, or an
        unknown id, is rejected.
        """
        entry = self._entries.get(message_id)
        if entry is None:
            raise KeyError(message_id)
        if entry.state != PENDING:
            raise ValueError("select requires a pending super chat")
        self._pending.remove(message_id)
        self._pending.appendleft(message_id)

    def skip(self) -> SuperChatRecord | None:
        """Manually advance the top presentation immediately.

        The current ``displayed`` Super Chat expires at the injected clock's
        current instant, and the next pending Super Chat is promoted to
        ``displayed`` immediately, bypassing the presentation interval. Returns
        the record that was skipped, or ``None`` when nothing was displayed.
        """
        now = self._clock()
        if self._top_id is None:
            return None
        top = self._entries[self._top_id]
        top.state = EXPIRED
        top.expired_at_ms = now
        self._top_id = None
        skipped = top.snapshot()
        if self._pending:
            front_id = self._pending.popleft()
            front = self._entries[front_id]
            front.state = DISPLAYED
            front.displayed_at_ms = now
            self._top_id = front_id
        return skipped

    def delete(self, message_id: str) -> SuperChatRecord | None:
        """Remove a pending or displayed Super Chat from presentation.

        The timeline item is retained and clearly marked ``deleted`` with the
        deletion instant (``deleted_at``) from the injected clock. A ``pending``
        Super Chat leaves the queue; a ``displayed`` Super Chat frees the top
        slot. Deleting an already deleted record is idempotent, deleting an
        expired record is a no-op (``None``), and an unknown id raises
        ``KeyError``.
        """
        entry = self._entries.get(message_id)
        if entry is None:
            raise KeyError(message_id)
        if entry.state == DELETED:
            return entry.snapshot()
        if entry.state == EXPIRED:
            return None
        now = self._clock()
        if entry.state == PENDING:
            self._pending.remove(message_id)
        else:  # DISPLAYED
            self._top_id = None
        entry.state = DELETED
        entry.deleted_at_ms = now
        return entry.snapshot()
