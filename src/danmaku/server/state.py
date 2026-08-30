"""Typed application-state keys shared across the service modules."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from danmaku.core.hub import DistributionHub

__all__ = [
    "ASSET_ROOT_KEY",
    "HELLO_TIMEOUT_KEY",
    "HUB_KEY",
    "SUBSCRIPTIONS_KEY",
    "WEBSOCKETS_KEY",
    "WS_DONE_KEY",
]

HUB_KEY = web.AppKey("hub", DistributionHub)
ASSET_ROOT_KEY = web.AppKey("asset_root", Path)
HELLO_TIMEOUT_KEY = web.AppKey("hello_timeout", float)
SUBSCRIPTIONS_KEY = web.AppKey("subscriptions", set)
WEBSOCKETS_KEY = web.AppKey("websockets", set)
WS_DONE_KEY = web.AppKey("ws_done", set)
