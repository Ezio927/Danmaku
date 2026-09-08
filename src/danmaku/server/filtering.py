"""Immutable per-startup OBS delivery filtering policy.

One :class:`FilteringPolicy` is created at service startup and shared by every
connected OBS client. It is frozen after construction: deny sets are normalized
(trimmed, Unicode-casefolded, deduplicated) and the ordinary-gift threshold is
fixed, so the exact same decision is reused for both the snapshot and the live
delivery paths.

The canonical host state (``SnapshotStore``) is never filtered; this module only
decides which messages are suppressed from OBS delivery. A message is suppressed
when any one of these independent predicates matches:

* the exact stable ``user.id`` is in ``deny_user_ids``;
* the trimmed, casefolded ``user.name`` exactly equals a normalized deny
  nickname (no substring matching);
* the message is a ``danmaku`` or ``superChat`` whose ``data.text`` contains a
  normalized deny keyword as a case-insensitive substring (no regex); or
* the message is an ordinary ``gift`` whose ``data.totalAmountMilliCny`` is below
  the fixed ``gift_threshold_milli_cny``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from danmaku.core.model import Message

__all__ = [
    "DEFAULT_GIFT_THRESHOLD_MILLI_CNY",
    "FilteringPolicy",
    "normalize_keywords",
    "normalize_nicknames",
]

DEFAULT_GIFT_THRESHOLD_MILLI_CNY = 100

_TEXT_KINDS = frozenset({"danmaku", "superChat"})


def _normalize_entries(entries: Iterable[str], field: str) -> frozenset[str]:
    """Trim, casefold, drop empty entries, and deduplicate deterministically."""
    normalized: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            raise TypeError(f"{field} entries must be strings")
        folded = entry.strip().casefold()
        if folded:
            normalized.add(folded)
    return frozenset(normalized)


def _coerce_user_ids(entries: Iterable[str]) -> frozenset[str]:
    """Collect exact, deduplicated user IDs (no trimming or casefolding)."""
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            raise TypeError("deny_user_ids entries must be strings")
        ids.add(entry)
    return frozenset(ids)


def normalize_nicknames(entries: Iterable[str]) -> frozenset[str]:
    """Return trimmed, casefolded, non-empty, deduplicated nickname denies.

    Matching is exact equality against the returned set: no substring matching
    is ever applied to nicknames.
    """
    return _normalize_entries(entries, "deny_nicknames")


def normalize_keywords(entries: Iterable[str]) -> frozenset[str]:
    """Return trimmed, casefolded, non-empty, deduplicated keyword denies.

    Matching is a case-insensitive substring search against ``data.text``.
    """
    return _normalize_entries(entries, "keywords")


def _fold_nickname(value: str) -> str:
    return value.strip().casefold()


def _validate_threshold(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("gift_threshold_milli_cny must be an integer")
    if value < 0:
        raise ValueError("gift_threshold_milli_cny must be >= 0")


@dataclass(frozen=True, slots=True)
class FilteringPolicy:
    """Frozen, normalized OBS delivery filtering policy.

    ``deny_user_ids`` is kept exactly (IDs are stable identifiers); nicknames
    and keywords are trimmed, Unicode-casefolded, and deduplicated. The ordinary
    gift threshold defaults to exactly 100 milli-CNY and filters only ``gift``
    events below it.
    """

    deny_user_ids: Iterable[str] = frozenset()
    deny_nicknames: Iterable[str] = frozenset()
    keywords: Iterable[str] = frozenset()
    gift_threshold_milli_cny: int = DEFAULT_GIFT_THRESHOLD_MILLI_CNY

    def __post_init__(self) -> None:
        object.__setattr__(self, "deny_user_ids", _coerce_user_ids(self.deny_user_ids))
        object.__setattr__(
            self, "deny_nicknames", normalize_nicknames(self.deny_nicknames)
        )
        object.__setattr__(self, "keywords", normalize_keywords(self.keywords))
        _validate_threshold(self.gift_threshold_milli_cny)

    def is_suppressed(self, message: Message) -> bool:
        """Return ``True`` when ``message`` must not be delivered to OBS clients."""
        if message.user.id in self.deny_user_ids:
            return True
        if _fold_nickname(message.user.name) in self.deny_nicknames:
            return True
        if message.kind in _TEXT_KINDS:
            folded_text = message.data["text"].casefold()
            if any(keyword in folded_text for keyword in self.keywords):
                return True
        if (
            message.kind == "gift"
            and message.data["totalAmountMilliCny"] < self.gift_threshold_milli_cny
        ):
            return True
        return False
