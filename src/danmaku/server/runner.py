"""Composition root: binds 127.0.0.1, runs the producer, owns the lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from pathlib import Path

from aiohttp import web

from danmaku.core.hub import DistributionHub
from danmaku.core.snapshot import SnapshotStore
from danmaku.mock.source import MockSource

from .app import SUBSCRIPTIONS_KEY, WS_DONE_KEY, create_app
from .config import ServiceConfig
from .filtering import FilteringPolicy

__all__ = ["Service"]

_SHUTDOWN_TIMEOUT = 5.0
_SUBSCRIPTION_CLOSE = 1001


class Service:
    """Runnable loopback-only HTTP/WebSocket service.

    Owns the hub, producer, and aiohttp runner. Bind conflicts are fatal and
    never select a different host or port silently.
    """

    def __init__(
        self,
        config: ServiceConfig,
        asset_root: Path | str | None = None,
        policy: FilteringPolicy | None = None,
    ) -> None:
        self._config = config
        self._asset_root = asset_root
        self._policy = policy if policy is not None else config.build_policy()
        self._hub = DistributionHub(
            store=SnapshotStore(max_messages=config.max_messages),
            capacity=config.max_messages,
            filter=self._policy.is_suppressed,
        )
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._producer: asyncio.Task | None = None

    @property
    def hub(self) -> DistributionHub:
        return self._hub

    @property
    def policy(self) -> FilteringPolicy:
        return self._policy

    @property
    def host(self) -> str:
        return self._config.host

    @property
    def port(self) -> int:
        return self._config.port

    async def start(self) -> None:
        self._app = create_app(hub=self._hub, asset_root=self._asset_root)
        self._runner = web.AppRunner(self._app, shutdown_timeout=_SHUTDOWN_TIMEOUT)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner, host=self._config.host, port=self._config.port
        )
        try:
            await site.start()
        except OSError:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
            self._runner = None
            self._app = None
            raise
        self._site = site
        self._producer = asyncio.create_task(self._produce())

    async def _produce(self) -> None:
        source = MockSource(
            cadence=self._config.cadence_milliseconds / 1000, start_sequence=1
        )
        async for message in source:
            self._hub.publish(message)

    async def stop(self) -> None:
        if self._producer is not None:
            self._producer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._producer
            self._producer = None

        # Flush pending aggregated gifts so no accepted gift disappears on
        # normal shutdown.
        self._hub.finalize()

        app = self._app
        if app is not None:
            for subscription in list(app[SUBSCRIPTIONS_KEY]):
                subscription.close(_SUBSCRIPTION_CLOSE)
            done_events = list(app[WS_DONE_KEY])
            if done_events:
                with contextlib.suppress(asyncio.TimeoutError, Exception):
                    await asyncio.wait_for(
                        asyncio.gather(*(event.wait() for event in done_events)),
                        timeout=_SHUTDOWN_TIMEOUT,
                    )

        if self._runner is not None:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
            self._runner = None
        self._app = None
        self._site = None

    async def run(self) -> None:
        await self.start()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        installed: list[int] = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
                installed.append(sig)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await stop_event.wait()
        finally:
            for sig in installed:
                loop.remove_signal_handler(sig)
            await self.stop()
