"""Application composition: routes and shared app state."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from danmaku.core.hub import DistributionHub

from .config_store import default_config_path
from .handlers import (
    asset_handler,
    health_handler,
    host_handler,
    obs_handler,
    settings_handler,
)
from .state import (
    ASSET_ROOT_KEY,
    CONFIG_PATH_KEY,
    HELLO_TIMEOUT_KEY,
    HUB_KEY,
    SUBSCRIPTIONS_KEY,
    WEBSOCKETS_KEY,
    WS_DONE_KEY,
)
from .websocket import (
    DEFAULT_HELLO_TIMEOUT,
    host_websocket_handler,
    websocket_handler,
)

__all__ = ["DEFAULT_ASSET_ROOT", "create_app"]

DEFAULT_ASSET_ROOT = Path(__file__).resolve().parent.parent / "web"


def create_app(
    hub: DistributionHub,
    asset_root: Path | str | None = None,
    hello_timeout: float = DEFAULT_HELLO_TIMEOUT,
    config_path: Path | str | None = None,
) -> web.Application:
    app = web.Application()
    app[HUB_KEY] = hub
    app[ASSET_ROOT_KEY] = (
        Path(asset_root) if asset_root is not None else DEFAULT_ASSET_ROOT
    )
    app[CONFIG_PATH_KEY] = (
        Path(config_path) if config_path is not None else default_config_path()
    )
    app[HELLO_TIMEOUT_KEY] = hello_timeout
    app[SUBSCRIPTIONS_KEY] = set()
    app[WEBSOCKETS_KEY] = set()
    app[WS_DONE_KEY] = set()

    app.router.add_get("/health", health_handler, allow_head=False)
    app.router.add_get("/obs", obs_handler, allow_head=False)
    app.router.add_get("/host", host_handler, allow_head=False)
    app.router.add_get("/host/settings", settings_handler, allow_head=False)
    app.router.add_post("/host/settings", settings_handler)
    app.router.add_get("/assets/{name}", asset_handler, allow_head=False)
    app.router.add_get("/ws", websocket_handler, allow_head=False)
    app.router.add_get("/ws/host", host_websocket_handler, allow_head=False)
    return app
