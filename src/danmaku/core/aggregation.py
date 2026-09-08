"""Deterministic five-second sliding-window aggregation for ordinary gifts.

This module is a pure core abstraction: it consumes canonical
:class:`~danmaku.core.model.Message` values and emits canonical
:class:`~danmaku.core.model.Message` values, with no knowledge of the OBS page,
the WebSocket protocol, the delivery filtering policy, or any transport
concern. Behaviour is documented in ``docs/gift-aggregation.md``:

* Only ``kind == "gift"`` messages are aggregated. Danmaku, guard, and
  super-chat messages pass through unchanged and are never merged with gifts
  or with one another.
* Gifts are grouped by the exact stable ``user.id`` and the exact
  ``data.giftName`` already exposed by the canonical v1 model. No platform
  combo field is invented.
* A gift merges into the current pending aggregate when it has the same key and
  its ``receivedAt`` is within ``window_milliseconds`` (default 5000) of the
  aggregate's current anchor — the most recently merged gift's ``receivedAt``.
  The boundary is inclusive: an exact five-second gap still merges. Merging
  sums the exact integer ``quantity`` and ``totalAmountMilliCny`` and extends
  the anchor to the new gift's ``receivedAt``.
* Any other input — a gift with a different key, a matching gift beyond the
  window, or a non-gift message — first finalizes (emits) the pending
  aggregate, then is processed. This keeps the delivered stream strictly
  increasing in ``sequence`` while non-gift messages retain their original
  identity and order.
* ``finalize()`` emits any remaining pending aggregate deterministically, so no
  accepted gift disappears on normal source shutdown or explicit finalization.

The emitted aggregate keeps the first gift's identity (``id``, ``sequence``,
``receivedAt``, ``source``, and ``user``) and replaces its ``data`` with the
exact summed ``quantity`` and ``totalAmountMilliCny``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .model import MAX_SEQUENCE, Message

__all__ = [
    "DEFAULT_WINDOW_MILLISECONDS",
    "GIFT_QUANTITY_MAX",
    "GiftAggregator",
]

DEFAULT_WINDOW_MILLISECONDS = 5000
GIFT_QUANTITY_MAX = 1_000_000

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _received_at_millis(value: str) -> int:
    """Return the exact integer millisecond offset of an RFC 3339 timestamp."""
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    delta = parsed - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def _would_overflow(pending: "_PendingGift", message: Message) -> bool:
    """Return ``True`` if merging ``message`` would exceed a model bound."""
    return (
        pending.quantity + message.data["quantity"] > GIFT_QUANTITY_MAX
        or pending.total_amount_milli_cny + message.data["totalAmountMilliCny"]
        > MAX_SEQUENCE
    )


@dataclass
class _PendingGift:
    """A not-yet-emitted aggregate: one group of matching gifts."""

    key: tuple[str, str]
    first: Message
    anchor_millis: int
    quantity: int
    total_amount_milli_cny: int

    def to_message(self) -> Message:
        return Message.from_dict(
            {
                "id": self.first.id,
                "sequence": self.first.sequence,
                "receivedAt": self.first.received_at,
                "source": self.first.source,
                "kind": "gift",
                "user": self.first.user.to_dict(),
                "data": {
                    "giftName": self.key[1],
                    "quantity": self.quantity,
                    "totalAmountMilliCny": self.total_amount_milli_cny,
                },
            }
        )


class GiftAggregator:
    """Group ordinary paid gifts by user and gift name within a sliding window.

    The aggregator is synchronous and deterministic: it holds at most one
    pending aggregate and emits that aggregate only when a boundary arrives
    (a non-gift message, a gift with a different key, or a matching gift beyond
    the window) or when :meth:`finalize` is called explicitly.
    """

    def __init__(self, window_milliseconds: int = DEFAULT_WINDOW_MILLISECONDS) -> None:
        if isinstance(window_milliseconds, bool) or not isinstance(
            window_milliseconds, int
        ):
            raise TypeError("window_milliseconds must be an integer")
        if window_milliseconds < 0:
            raise ValueError("window_milliseconds must be >= 0")
        self._window_milliseconds = window_milliseconds
        self._pending: _PendingGift | None = None

    @property
    def window_milliseconds(self) -> int:
        return self._window_milliseconds

    @property
    def has_pending(self) -> bool:
        """Return ``True`` when an aggregate is still waiting to be emitted."""
        return self._pending is not None

    def accept(self, message: Message) -> tuple[Message, ...]:
        """Accept one message and return the delivery messages to emit.

        A non-gift message first flushes any pending aggregate and then returns
        itself unchanged. A matching gift within the window is absorbed into the
        pending aggregate (returning nothing). Any other gift first flushes the
        pending aggregate and then opens a new group.
        """
        if not isinstance(message, Message):
            raise TypeError("accept requires a Message")

        if message.kind != "gift":
            emitted = self._flush()
            return emitted + (message,)

        key = (message.user.id, message.data["giftName"])
        arrived = _received_at_millis(message.received_at)
        pending = self._pending

        if (
            pending is not None
            and pending.key == key
            and arrived - pending.anchor_millis <= self._window_milliseconds
            and not _would_overflow(pending, message)
        ):
            pending.quantity += message.data["quantity"]
            pending.total_amount_milli_cny += message.data["totalAmountMilliCny"]
            pending.anchor_millis = arrived
            return ()

        emitted = self._flush()
        self._pending = _PendingGift(
            key=key,
            first=message,
            anchor_millis=arrived,
            quantity=message.data["quantity"],
            total_amount_milli_cny=message.data["totalAmountMilliCny"],
        )
        return emitted

    def finalize(self) -> tuple[Message, ...]:
        """Emit the pending aggregate, if any, and clear it. Idempotent."""
        return self._flush()

    def _flush(self) -> tuple[Message, ...]:
        pending = self._pending
        if pending is None:
            return ()
        self._pending = None
        return (pending.to_message(),)
