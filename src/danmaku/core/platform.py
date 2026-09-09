"""Internal platform gift metadata carried at the adapter-to-aggregation boundary.

This module defines value objects that are deliberately *not* part of the
canonical v1 message model and are never serialized into a protocol frame. They
carry the reliable recorded Bilibili platform combo identity and cumulative gift
fields so the gift aggregator can group an ordinary paid gift combo by its
platform identity and apply cumulative quantity/amount updates deterministically
without double-counting cumulative values.

When a gift carries no :class:`GiftPlatformMeta`, the aggregator falls back to
the existing bounded five-second sliding-window behaviour, so missing or
unusable platform metadata never changes the deterministic fallback semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "GIFT_QUANTITY_MAX",
    "GiftPlatformMeta",
    "PLATFORM_CUMULATIVE_MAX",
]

#: The canonical ordinary-gift quantity bound, shared with the gift aggregator.
GIFT_QUANTITY_MAX = 1_000_000

#: The canonical cumulative-amount bound (milli-CNY), matching the model's
#: ``MAX_SEQUENCE`` so a cumulative total always stays serializable in v1.
PLATFORM_CUMULATIVE_MAX = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class GiftPlatformMeta:
    """Reliable recorded platform combo identity and cumulative gift fields.

    ``combo_id`` is the stable platform combo identity of one ordinary paid gift
    burst. ``cumulative_quantity`` and ``cumulative_amount_milli_cny`` are the
    platform-reported running totals for that combo so far (an integer count and
    an integer one-thousandth-CNY amount). Both cumulative values are inclusive
    of the current event, so the aggregator applies only the delta between two
    events of the same combo and never re-sums the cumulative values.
    """

    combo_id: str
    cumulative_quantity: int
    cumulative_amount_milli_cny: int

    def __post_init__(self) -> None:
        if not isinstance(self.combo_id, str) or not self.combo_id:
            raise ValueError("combo_id must be a non-empty string")
        if any(ord(char) < 0x20 for char in self.combo_id):
            raise ValueError("combo_id must not contain control characters")
        if (
            isinstance(self.cumulative_quantity, bool)
            or not isinstance(self.cumulative_quantity, int)
            or not 1 <= self.cumulative_quantity <= GIFT_QUANTITY_MAX
        ):
            raise ValueError(
                f"cumulative_quantity must be an integer between 1 and "
                f"{GIFT_QUANTITY_MAX}"
            )
        if (
            isinstance(self.cumulative_amount_milli_cny, bool)
            or not isinstance(self.cumulative_amount_milli_cny, int)
            or not 0
            <= self.cumulative_amount_milli_cny
            <= PLATFORM_CUMULATIVE_MAX
        ):
            raise ValueError(
                f"cumulative_amount_milli_cny must be an integer between 0 and "
                f"{PLATFORM_CUMULATIVE_MAX}"
            )
