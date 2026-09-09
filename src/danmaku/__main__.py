"""Runnable entry point: run the loopback-only HTTP/WebSocket service."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
from collections.abc import Sequence
from pathlib import Path

from danmaku.server.config import (
    DEFAULT_CADENCE_MILLISECONDS,
    DEFAULT_PORT,
    ConfigError,
    ServiceConfig,
)
from danmaku.server.config_store import default_config_path, load_config
from danmaku.server.filtering import DEFAULT_GIFT_THRESHOLD_MILLI_CNY
from danmaku.server.runner import Service

#: Exit status returned when the configured loopback port cannot be bound.
#: The service fails closed: it never selects a different host or port.
EXIT_BIND_FAILURE = 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m danmaku",
        description="Run the loopback-only Danmaku first vertical slice service.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"listen port on 127.0.0.1 (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--cadence-milliseconds",
        type=int,
        default=None,
        help=(
            "mock message interval in milliseconds "
            f"(default: {DEFAULT_CADENCE_MILLISECONDS})"
        ),
    )
    parser.add_argument(
        "--gift-threshold-milli-cny",
        type=int,
        default=None,
        help=(
            "ordinary gift delivery threshold in milli-CNY; gifts below it are "
            f"suppressed (default: {DEFAULT_GIFT_THRESHOLD_MILLI_CNY})"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "path to the persisted JSON configuration file "
            "(default: the deterministic local location, or $DANMAKU_CONFIG)"
        ),
    )
    return parser.parse_args(argv)


def _resolve_config_path(args: argparse.Namespace) -> Path:
    if args.config is not None:
        return args.config
    return default_config_path()


def _apply_overrides(
    config: ServiceConfig, args: argparse.Namespace
) -> ServiceConfig:
    """Apply explicit CLI overrides on top of a loaded configuration.

    Precedence is canonical defaults < persisted values < explicit CLI flags.
    Only flags that were actually provided override; all others keep their
    loaded value. Overrides are revalidated by :class:`ServiceConfig`.
    """
    overrides: dict[str, int] = {}
    if args.port is not None:
        overrides["port"] = args.port
    if args.cadence_milliseconds is not None:
        overrides["cadence_milliseconds"] = args.cadence_milliseconds
    if args.gift_threshold_milli_cny is not None:
        overrides["gift_threshold_milli_cny"] = args.gift_threshold_milli_cny
    if overrides:
        return dataclasses.replace(config, **overrides)
    return config


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    config_path = _resolve_config_path(args)
    result = load_config(config_path)
    if result.diagnostic:
        print(f"warning: {result.diagnostic}", file=sys.stderr)

    try:
        config = _apply_overrides(result.config, args)
        policy = config.build_policy()
    except (ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    service = Service(config, policy=policy, config_path=config_path)
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        pass
    except OSError:
        print(
            f"error: cannot bind to {config.host}:{config.port}; "
            "the address is already in use. Stop the conflicting process or "
            "choose a different --port and try again.",
            file=sys.stderr,
        )
        return EXIT_BIND_FAILURE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
