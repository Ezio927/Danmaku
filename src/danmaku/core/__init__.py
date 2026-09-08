"""Canonical core: model, aggregation, bounded snapshot, and distribution hub."""

from .aggregation import DEFAULT_WINDOW_MILLISECONDS, GiftAggregator
from .hub import DistributionHub, Subscription, SubscriptionClosed
from .model import Message, User, ValidationError
from .snapshot import SnapshotStore

__all__ = [
    "DEFAULT_WINDOW_MILLISECONDS",
    "DistributionHub",
    "GiftAggregator",
    "Message",
    "SnapshotStore",
    "Subscription",
    "SubscriptionClosed",
    "User",
    "ValidationError",
]
