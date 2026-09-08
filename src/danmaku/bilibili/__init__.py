"""Offline recorded-Bilibili event normalization.

This package adapts a documented, recorded Bilibili input envelope into the
canonical Danmaku message model. It is deterministic and offline-only: it
validates a frozen fixture contract and maps supported danmaku, gift, guard,
and super-chat events into ``source="bilibili"`` messages. There is no network
access, credential handling, or live transport here.
"""

from .adapter import BilibiliAdapter, BilibiliAdapterError, DuplicateEventError

__all__ = ["BilibiliAdapter", "BilibiliAdapterError", "DuplicateEventError"]
