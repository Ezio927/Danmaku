"""Installation, startup, and package-resource tests.

These tests verify that the documented ``src/``-layout project install is
reproducible and that the packaged OBS web assets are resolvable and servable
from an installed environment, without introducing any runtime dependency:

- ``pyproject.toml`` declares the web assets as setuptools package data under
  the ``danmaku`` package and locates packages under ``src/``.
- The three allow-listed web assets are present and resolvable both as package
  resources (``importlib.resources``) and through the runtime
  ``DEFAULT_ASSET_ROOT`` used by ``python -m danmaku``.
- The ``python -m danmaku`` entry point parses its documented startup flags.
- A loopback port that is already in use fails startup with a concise, safe
  error (naming the loopback address and port) and a deterministic non-zero
  exit status, never selecting another port.
"""

from __future__ import annotations

import dataclasses
import importlib.resources
import json
import os
import socket
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.server.app import DEFAULT_ASSET_ROOT, create_app  # noqa: E402
from danmaku.server.config import ConfigError, ServiceConfig  # noqa: E402
from danmaku.server.config_store import default_config_path  # noqa: E402
from danmaku.server.diagnostics import LOG_FILENAME  # noqa: E402

WEB_ASSETS = ("index.html", "app.js", "style.css")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class PackagingMetadataTests(unittest.TestCase):
    def test_packages_find_uses_src_layout(self):
        find = _pyproject()["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(find["where"], ["src"])

    def test_aiohttp_is_declared_dependency(self):
        dependencies = _pyproject()["project"]["dependencies"]
        self.assertTrue(
            any(str(dep).startswith("aiohttp") for dep in dependencies),
            "aiohttp must be declared as the runtime dependency",
        )

    def test_web_assets_declared_as_package_data(self):
        package_data = _pyproject()["tool"]["setuptools"]["package-data"]
        declared = package_data["danmaku"]
        for name in WEB_ASSETS:
            self.assertIn(f"web/{name}", declared)


class PackageResourceTests(unittest.TestCase):
    def test_web_assets_resolvable_as_package_resources(self):
        package_root = importlib.resources.files("danmaku")
        for name in WEB_ASSETS:
            with self.subTest(name=name):
                resource = package_root.joinpath("web", name)
                self.assertTrue(resource.is_file(), f"{name} must be packaged")

    def test_default_asset_root_contains_all_allow_listed_assets(self):
        self.assertTrue(DEFAULT_ASSET_ROOT.is_dir())
        for name in WEB_ASSETS:
            with self.subTest(name=name):
                self.assertTrue(
                    (DEFAULT_ASSET_ROOT / name).is_file(),
                    f"{name} must exist under {DEFAULT_ASSET_ROOT}",
                )


class EntryPointStartupTests(unittest.TestCase):
    def test_module_entry_point_prints_help_and_exits_zero(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-m", "danmaku", "--help"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--port", result.stdout)
        self.assertIn("--cadence-milliseconds", result.stdout)
        self.assertIn("--gift-threshold-milli-cny", result.stdout)
        self.assertIn("--config", result.stdout)

    def test_parse_args_defaults_are_none_for_precedence(self):
        from danmaku.__main__ import _parse_args

        args = _parse_args([])
        self.assertIsNone(args.port)
        self.assertIsNone(args.cadence_milliseconds)
        self.assertIsNone(args.gift_threshold_milli_cny)
        self.assertIsNone(args.config)

    def test_config_flag_is_parsed(self):
        from danmaku.__main__ import _parse_args

        args = _parse_args(["--config", "/tmp/example/config.json"])
        self.assertEqual(args.config, Path("/tmp/example/config.json"))

    def test_gift_threshold_flag_is_parsed(self):
        from danmaku.__main__ import _parse_args

        args = _parse_args(["--gift-threshold-milli-cny", "500"])
        self.assertEqual(args.gift_threshold_milli_cny, 500)


class BindFailureStartupTests(unittest.TestCase):
    """The CLI boundary fails closed and reports bind conflicts safely.

    A held loopback port forces ``python -m danmaku`` to fail its bind; the
    process must print a concise, actionable message naming the loopback
    address and configured port (never a raw traceback or exception detail)
    and exit with the deterministic non-zero bind-failure status.
    """

    def test_bind_conflict_reports_concise_error_and_nonzero_exit(self):
        from danmaku.__main__ import EXIT_BIND_FAILURE

        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            port = blocker.getsockname()[1]

            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT / "src")
            with tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "danmaku",
                        "--config",
                        str(Path(tmp) / "config.json"),
                        "--port",
                        str(port),
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )

        self.assertEqual(result.returncode, EXIT_BIND_FAILURE)
        self.assertIn("127.0.0.1", result.stderr)
        self.assertIn(str(port), result.stderr)
        self.assertIn("--port", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("OSError", result.stderr)
        self.assertNotIn("Errno", result.stderr)


class StartupPrecedenceTests(unittest.TestCase):
    def test_resolve_config_path_prefers_flag(self):
        from danmaku.__main__ import _parse_args, _resolve_config_path

        args = _parse_args(["--config", "/tmp/example/config.json"])
        self.assertEqual(_resolve_config_path(args), Path("/tmp/example/config.json"))

    def test_resolve_config_path_falls_back_to_default(self):
        from danmaku.__main__ import _parse_args, _resolve_config_path

        self.assertEqual(_resolve_config_path(_parse_args([])), default_config_path())

    def test_apply_overrides_cli_wins_over_persisted(self):
        from danmaku.__main__ import _apply_overrides, _parse_args

        persisted = dataclasses.replace(
            ServiceConfig.default(), port=18000, gift_threshold_milli_cny=500
        )
        merged = _apply_overrides(persisted, _parse_args(["--port", "19000"]))
        self.assertEqual(merged.port, 19000)
        # threshold was not provided on the CLI, so its persisted value survives
        self.assertEqual(merged.gift_threshold_milli_cny, 500)

    def test_apply_overrides_without_flags_keeps_persisted(self):
        from danmaku.__main__ import _apply_overrides, _parse_args

        persisted = dataclasses.replace(ServiceConfig.default(), port=18000)
        merged = _apply_overrides(persisted, _parse_args([]))
        self.assertEqual(merged.port, 18000)
        self.assertEqual(merged.cadence_milliseconds, 1000)

    def test_apply_overrides_rejects_out_of_bound_cli_value(self):
        from danmaku.__main__ import _apply_overrides, _parse_args

        with self.assertRaises(ConfigError):
            _apply_overrides(ServiceConfig.default(), _parse_args(["--port", "70000"]))


class SettingsRouteRegistrationTests(unittest.TestCase):
    def test_create_app_registers_settings_routes(self):
        from danmaku.core.hub import DistributionHub

        app = create_app(hub=DistributionHub())
        methods_by_path: dict[str, set[str]] = {}
        for route in app.router.routes():
            if route.resource is not None:
                methods_by_path.setdefault(route.resource.canonical, set()).add(
                    route.method
                )
        self.assertIn("/host/settings", methods_by_path)
        self.assertEqual(methods_by_path["/host/settings"], {"GET", "POST"})

    def test_create_app_accepts_explicit_config_path(self):
        from danmaku.core.hub import DistributionHub
        from danmaku.server.state import CONFIG_PATH_KEY

        app = create_app(
            hub=DistributionHub(), config_path="/tmp/example/config.json"
        )
        self.assertEqual(app[CONFIG_PATH_KEY], Path("/tmp/example/config.json"))


class EntryPointDiagnosticsTests(unittest.TestCase):
    """The CLI boundary writes a safe local operational log next to the config.

    On bind failure the entry point records exactly one allow-listed
    ``bind_failure`` event; on configuration fallback it also records a
    ``config_fallback`` event. Both land in the deterministic local log file
    and never include a traceback or any private content.
    """

    def _run(self, config_path: Path, port: int) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "danmaku",
                "--config",
                str(config_path),
                "--port",
                str(port),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def _read_records(self, log_path: Path) -> list[dict]:
        if not log_path.exists():
            return []
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_bind_failure_writes_bind_failure_record(self):
        from danmaku.__main__ import EXIT_BIND_FAILURE

        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            port = blocker.getsockname()[1]
            with tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "config.json"
                result = self._run(config_path, port)
                self.assertEqual(result.returncode, EXIT_BIND_FAILURE)
                records = self._read_records(Path(tmp) / LOG_FILENAME)

        self.assertEqual([r["event"] for r in records], ["bind_failure"])
        self.assertEqual(records[0]["host"], "127.0.0.1")
        self.assertEqual(records[0]["port"], port)

    def test_config_fallback_writes_config_fallback_record(self):
        from danmaku.__main__ import EXIT_BIND_FAILURE

        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            port = blocker.getsockname()[1]
            with tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "config.json"
                config_path.write_text("{not json", encoding="utf-8")
                result = self._run(config_path, port)
                self.assertEqual(result.returncode, EXIT_BIND_FAILURE)
                records = self._read_records(Path(tmp) / LOG_FILENAME)

        self.assertEqual(
            [r["event"] for r in records], ["config_fallback", "bind_failure"]
        )
        self.assertEqual(records[0]["source"], "defaults")
        self.assertIn("reason", records[0])


if __name__ == "__main__":
    unittest.main()
