"""Runnable entry point: run the loopback-only HTTP/WebSocket service."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
from collections.abc import Sequence

from danmaku.server.config import (
    DEFAULT_CADENCE_MILLISECONDS,
    DEFAULT_PORT,
    ConfigError,
    ServiceConfig,
)
from danmaku.server.runner import Service


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m danmaku",
        description="Run the loopback-only Danmaku first vertical slice service.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"listen port on 127.0.0.1 (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--cadence-milliseconds",
        type=int,
        default=DEFAULT_CADENCE_MILLISECONDS,
        help=(
            "mock message interval in milliseconds "
            f"(default: {DEFAULT_CADENCE_MILLISECONDS})"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = dataclasses.replace(
            ServiceConfig.default(),
            port=args.port,
            cadence_milliseconds=args.cadence_milliseconds,
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    service = Service(config)
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
