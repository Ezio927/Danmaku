"""Canonical core: model, aggregation, bounded snapshot, distribution hub."""

from .aggregation import DEFAULT_WINDOW_MILLISECONDS, GiftAggregator
from .hub import DistributionHub, Subscription, SubscriptionClosed
from .model import Message, User, ValidationError
from .snapshot import SnapshotStore
from .superchat import (
    DELETED,
    DISPLAYED,
    EXPIRED,
    PENDING,
    PRESENTATION_INTERVAL_MILLISECONDS,
    PRESENTATION_INTERVAL_SECONDS,
    SuperChatLifecycle,
    SuperChatRecord,
)

__all__ = [
    "DEFAULT_WINDOW_MILLISECONDS",
    "DELETED",
    "DISPLAYED",
    "DistributionHub",
    "EXPIRED",
    "GiftAggregator",
    "Message",
    "PENDING",
    "PRESENTATION_INTERVAL_MILLISECONDS",
    "PRESENTATION_INTERVAL_SECONDS",
    "SnapshotStore",
    "Subscription",
    "SubscriptionClosed",
    "SuperChatLifecycle",
    "SuperChatRecord",
    "User",
    "ValidationError",
]
