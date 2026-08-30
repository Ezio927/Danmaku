"""Loopback-only HTTP/WebSocket service composing the canonical core with aiohttp."""

from .app import create_app
from .config import ConfigError, ServiceConfig
from .runner import Service

__all__ = ["ConfigError", "Service", "ServiceConfig", "create_app"]
