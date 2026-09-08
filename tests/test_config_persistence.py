"""Configuration persistence tests.

Covers the safe, versioned local JSON store: the deterministic default
location, canonical defaults on a missing file, current-version round-trips
with OBS filtering values preserved, deterministic rejection of malformed /
unsupported / wrongly-typed / unknown / out-of-bound values, atomic same-
filesystem saves with a single retained valid backup, and the documented
``defaults < persisted < CLI`` precedence at the configuration level.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.server.config import CONFIG_VERSION, ConfigError, ServiceConfig  # noqa: E402
from danmaku.server.config_store import (  # noqa: E402
    BACKUP_SUFFIX,
    PRIMARY_FILENAME,
    default_config_path,
    load_config,
    save_config,
)
from danmaku.server.filtering import DEFAULT_GIFT_THRESHOLD_MILLI_CNY  # noqa: E402


def _valid_dict() -> dict:
    return {
        "configVersion": 1,
        "service": {"host": "127.0.0.1", "port": 17391},
        "mock": {"cadenceMilliseconds": 1000},
        "snapshot": {"maxMessages": 100},
        "obs": {
            "denyUserIds": [],
            "denyNicknames": [],
            "keywords": [],
            "giftThresholdMilliCny": 100,
        },
    }


class DefaultPathTests(unittest.TestCase):
    def test_default_posix_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}, clear=True):
                path = default_config_path()
        self.assertEqual(path, Path(tmp) / ".config" / "danmaku" / PRIMARY_FILENAME)

    def test_xdg_config_home_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"HOME": "/nonexistent", "XDG_CONFIG_HOME": tmp}, clear=True
            ):
                path = default_config_path()
        self.assertEqual(path, Path(tmp) / "danmaku" / PRIMARY_FILENAME)

    def test_env_var_overrides_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom" / "cfg.json"
            with mock.patch.dict(os.environ, {"DANMAKU_CONFIG": str(target)}, clear=True):
                path = default_config_path()
        self.assertEqual(path, target)


class LoadDefaultsTests(unittest.TestCase):
    def test_missing_config_loads_canonical_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = load_config(Path(tmp) / "config.json")
        self.assertEqual(result.source, "defaults")
        self.assertIsNone(result.diagnostic)
        config = result.config
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 17391)
        self.assertEqual(config.cadence_milliseconds, 1000)
        self.assertEqual(config.max_messages, 100)

    def test_defaults_include_100_milli_cny_threshold_and_empty_deny_lists(self):
        config = ServiceConfig.default()
        self.assertEqual(config.gift_threshold_milli_cny, 100)
        self.assertEqual(config.gift_threshold_milli_cny, DEFAULT_GIFT_THRESHOLD_MILLI_CNY)
        self.assertEqual(config.deny_user_ids, frozenset())
        self.assertEqual(config.deny_nicknames, frozenset())
        self.assertEqual(config.keywords, frozenset())


class RoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_round_trip(self):
        config = ServiceConfig(
            port=18000,
            cadence_milliseconds=2000,
            deny_user_ids=frozenset({"user:alice", "user:bob"}),
            deny_nicknames=frozenset({"Carol"}),
            keywords=frozenset({"secret", "spoiler"}),
            gift_threshold_milli_cny=500,
        )
        data = config.to_dict()
        self.assertEqual(data["configVersion"], CONFIG_VERSION)
        self.assertEqual(ServiceConfig.from_dict(data), config)

    def test_save_load_round_trip_preserves_obs_filtering(self):
        config = ServiceConfig(
            port=18000,
            deny_user_ids=frozenset({"user:alice", "user:bob"}),
            deny_nicknames=frozenset({"Carol"}),
            keywords=frozenset({"secret", "spoiler"}),
            gift_threshold_milli_cny=500,
        )
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            save_config(config, primary)
            result = load_config(primary)
        self.assertEqual(result.source, "primary")
        loaded = result.config
        self.assertEqual(loaded.port, 18000)
        self.assertEqual(loaded.deny_user_ids, frozenset({"user:alice", "user:bob"}))
        self.assertEqual(loaded.deny_nicknames, frozenset({"Carol"}))
        self.assertEqual(loaded.keywords, frozenset({"secret", "spoiler"}))
        self.assertEqual(loaded.gift_threshold_milli_cny, 500)

    def test_persisted_file_carries_explicit_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            save_config(ServiceConfig(), primary)
            data = json.loads(primary.read_text(encoding="utf-8"))
        self.assertEqual(data["configVersion"], CONFIG_VERSION)

    def test_to_dict_sorts_deny_lists_deterministically(self):
        config = ServiceConfig(
            deny_user_ids=frozenset({"user:b", "user:a"}),
            keywords=frozenset({"z", "a"}),
        )
        data = config.to_dict()
        self.assertEqual(data["obs"]["denyUserIds"], ["user:a", "user:b"])
        self.assertEqual(data["obs"]["keywords"], ["a", "z"])


class RejectionTests(unittest.TestCase):
    def _assert_rejected(self, mutate):
        value = _valid_dict()
        mutate(value)
        with self.assertRaises(ConfigError):
            ServiceConfig.from_dict(value)

    def test_future_version_rejected(self):
        self._assert_rejected(lambda v: v.__setitem__("configVersion", 2))

    def test_unsupported_version_type_rejected(self):
        self._assert_rejected(lambda v: v.__setitem__("configVersion", "1"))

    def test_missing_obs_section_rejected(self):
        self._assert_rejected(lambda v: v.pop("obs"))

    def test_unknown_obs_key_rejected(self):
        self._assert_rejected(lambda v: v["obs"].__setitem__("secret", []))

    def test_deny_user_ids_must_be_array(self):
        self._assert_rejected(lambda v: v["obs"].__setitem__("denyUserIds", "user:alice"))

    def test_deny_user_ids_entries_must_be_strings(self):
        self._assert_rejected(lambda v: v["obs"].__setitem__("denyUserIds", ["a", 1]))

    def test_deny_nicknames_entries_must_be_strings(self):
        self._assert_rejected(lambda v: v["obs"].__setitem__("denyNicknames", [None]))

    def test_keywords_must_be_array(self):
        self._assert_rejected(lambda v: v["obs"].__setitem__("keywords", {"x": 1}))

    def test_gift_threshold_must_be_integer(self):
        self._assert_rejected(
            lambda v: v["obs"].__setitem__("giftThresholdMilliCny", "100")
        )

    def test_gift_threshold_boolean_rejected(self):
        self._assert_rejected(
            lambda v: v["obs"].__setitem__("giftThresholdMilliCny", True)
        )

    def test_gift_threshold_negative_rejected(self):
        self._assert_rejected(
            lambda v: v["obs"].__setitem__("giftThresholdMilliCny", -1)
        )

    def test_out_of_bound_port_rejected(self):
        self._assert_rejected(lambda v: v["service"].__setitem__("port", 65536))

    def test_port_wrong_type_rejected(self):
        self._assert_rejected(lambda v: v["service"].__setitem__("port", 17391.0))
        self._assert_rejected(lambda v: v["service"].__setitem__("port", "17391"))

    def test_cadence_wrong_type_rejected(self):
        self._assert_rejected(
            lambda v: v["mock"].__setitem__("cadenceMilliseconds", 1000.0)
        )
        self._assert_rejected(
            lambda v: v["mock"].__setitem__("cadenceMilliseconds", "1000")
        )

    def test_max_messages_wrong_type_rejected(self):
        self._assert_rejected(lambda v: v["snapshot"].__setitem__("maxMessages", 100.0))
        self._assert_rejected(lambda v: v["snapshot"].__setitem__("maxMessages", "100"))

    def test_malformed_json_falls_back_to_defaults_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            primary.write_text("{not json", encoding="utf-8")
            result = load_config(primary)
        self.assertEqual(result.source, "defaults")
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.config, ServiceConfig.default())

    def test_non_finite_number_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            primary.write_text(
                '{"configVersion":1,"service":{"host":"127.0.0.1","port":NaN},'
                '"mock":{"cadenceMilliseconds":1000},"snapshot":{"maxMessages":100},'
                '"obs":{"denyUserIds":[],"denyNicknames":[],"keywords":[],'
                '"giftThresholdMilliCny":100}}',
                encoding="utf-8",
            )
            result = load_config(primary)
        self.assertEqual(result.source, "defaults")
        self.assertIsNotNone(result.diagnostic)


class FallbackTests(unittest.TestCase):
    def _primary(self, tmp: str) -> Path:
        return Path(tmp) / "config.json"

    def _backup(self, tmp: str) -> Path:
        return Path(tmp) / ("config.json" + BACKUP_SUFFIX)

    def test_corrupt_primary_uses_valid_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = self._primary(tmp)
            save_config(ServiceConfig(port=18000), primary)
            save_config(ServiceConfig(port=19000), primary)
            # corrupt the primary; backup still holds the previous valid config
            primary.write_text("corrupt", encoding="utf-8")
            result = load_config(primary)
        self.assertEqual(result.source, "backup")
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.config.port, 18000)

    def test_corrupt_primary_and_backup_use_defaults_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = self._primary(tmp)
            primary.write_text("corrupt", encoding="utf-8")
            self._backup(tmp).write_text("also corrupt", encoding="utf-8")
            result = load_config(primary)
        self.assertEqual(result.source, "defaults")
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.config, ServiceConfig.default())

    def test_corrupt_primary_without_backup_uses_defaults_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = self._primary(tmp)
            primary.write_text("corrupt", encoding="utf-8")
            result = load_config(primary)
        self.assertEqual(result.source, "defaults")
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.config, ServiceConfig.default())

    def test_missing_primary_ignores_stale_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = self._primary(tmp)
            save_config(ServiceConfig(port=18000), primary)
            save_config(ServiceConfig(port=19000), primary)  # backup == 18000
            primary.unlink()  # primary missing, backup remains
            result = load_config(primary)
        self.assertEqual(result.source, "defaults")
        self.assertIsNone(result.diagnostic)
        self.assertEqual(result.config.port, 17391)

    def test_primary_as_directory_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = self._primary(tmp)
            primary.mkdir()
            result = load_config(primary)
        self.assertEqual(result.source, "defaults")
        self.assertIsNotNone(result.diagnostic)


class SaveTests(unittest.TestCase):
    def test_first_save_writes_primary_and_no_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            save_config(ServiceConfig(port=18000), primary)
            self.assertTrue(primary.is_file())
            self.assertFalse((Path(tmp) / ("config.json" + BACKUP_SUFFIX)).exists())
            self.assertEqual(load_config(primary).config.port, 18000)

    def test_save_retains_one_previous_valid_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            backup = Path(tmp) / ("config.json" + BACKUP_SUFFIX)
            save_config(ServiceConfig(port=18000), primary)
            save_config(ServiceConfig(port=19000), primary)
            save_config(ServiceConfig(port=20000), primary)
            self.assertEqual(load_config(primary).config.port, 20000)
            self.assertEqual(load_config(backup).config.port, 19000)

    def test_save_does_not_back_up_corrupt_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            backup = Path(tmp) / ("config.json" + BACKUP_SUFFIX)
            save_config(ServiceConfig(port=18000), primary)
            save_config(ServiceConfig(port=19000), primary)  # backup == 18000
            primary.write_text("corrupt", encoding="utf-8")
            save_config(ServiceConfig(port=20000), primary)
            self.assertEqual(load_config(backup).config.port, 18000)
            self.assertEqual(load_config(primary).config.port, 20000)

    def test_save_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            save_config(ServiceConfig(), primary)
            save_config(ServiceConfig(port=18000), primary)
            names = [p.name for p in Path(tmp).iterdir()]
            self.assertTrue(all(".tmp" not in name for name in names))

    def test_save_failure_preserves_existing_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            save_config(ServiceConfig(port=18000), primary)
            before = primary.read_bytes()
            with mock.patch("os.replace", side_effect=OSError("rename failed")):
                with self.assertRaises(OSError):
                    save_config(ServiceConfig(port=19000), primary)
            self.assertEqual(primary.read_bytes(), before)
            self.assertEqual(load_config(primary).config.port, 18000)
            names = [p.name for p in Path(tmp).iterdir()]
            self.assertTrue(all(".tmp" not in name for name in names))


class PrecedenceTests(unittest.TestCase):
    def test_defaults_then_persisted_then_cli(self):
        defaults = ServiceConfig.default()
        self.assertEqual(defaults.port, 17391)
        self.assertEqual(defaults.gift_threshold_milli_cny, 100)

        with tempfile.TemporaryDirectory() as tmp:
            primary = Path(tmp) / "config.json"
            save_config(
                ServiceConfig(port=18000, gift_threshold_milli_cny=500), primary
            )
            persisted = load_config(primary).config

        # CLI port overrides persisted port; threshold keeps its persisted value
        merged = dataclasses.replace(persisted, port=19000)
        self.assertEqual(merged.port, 19000)
        self.assertEqual(merged.gift_threshold_milli_cny, 500)

        # CLI threshold overrides persisted threshold
        merged = dataclasses.replace(persisted, gift_threshold_milli_cny=600)
        self.assertEqual(merged.port, 18000)
        self.assertEqual(merged.gift_threshold_milli_cny, 600)

    def test_cli_override_revalidates(self):
        with self.assertRaises(ConfigError):
            dataclasses.replace(ServiceConfig.default(), port=70000)
        with self.assertRaises(ConfigError):
            dataclasses.replace(ServiceConfig.default(), gift_threshold_milli_cny=-1)


class PolicyCompositionTests(unittest.TestCase):
    def test_build_policy_normalizes_from_raw_settings(self):
        config = ServiceConfig(
            deny_nicknames=frozenset({" Alice ", "ALICE", ""}),
            keywords=frozenset({"Hello", "hello"}),
            gift_threshold_milli_cny=500,
        )
        policy = config.build_policy()
        self.assertEqual(policy.deny_nicknames, frozenset({"alice"}))
        self.assertEqual(policy.keywords, frozenset({"hello"}))
        self.assertEqual(policy.gift_threshold_milli_cny, 500)
        self.assertIsInstance(policy.deny_user_ids, frozenset)
        self.assertIsInstance(policy.deny_nicknames, frozenset)
        self.assertIsInstance(policy.keywords, frozenset)

    def test_build_policy_from_defaults_is_canonical(self):
        policy = ServiceConfig.default().build_policy()
        self.assertEqual(policy.deny_user_ids, frozenset())
        self.assertEqual(policy.deny_nicknames, frozenset())
        self.assertEqual(policy.keywords, frozenset())
        self.assertEqual(policy.gift_threshold_milli_cny, 100)


if __name__ == "__main__":
    unittest.main()
