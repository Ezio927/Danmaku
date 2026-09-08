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
"""

from __future__ import annotations

import importlib.resources
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.server.app import DEFAULT_ASSET_ROOT  # noqa: E402

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

    def test_parse_args_defaults_match_documented_values(self):
        from danmaku.__main__ import _parse_args

        args = _parse_args([])
        self.assertEqual(args.port, 17391)
        self.assertEqual(args.cadence_milliseconds, 1000)
        self.assertEqual(args.gift_threshold_milli_cny, 100)

    def test_gift_threshold_flag_is_parsed(self):
        from danmaku.__main__ import _parse_args

        args = _parse_args(["--gift-threshold-milli-cny", "500"])
        self.assertEqual(args.gift_threshold_milli_cny, 500)


if __name__ == "__main__":
    unittest.main()
