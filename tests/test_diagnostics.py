"""Operational diagnostics boundary tests.

Covers the small, allow-listed, local-only logging surface: canonical 10 MiB /
five-backup rotation, the explicit and bounded ``backup_count=0`` behavior,
strict event/field allow-listing, value redaction, lifecycle records at the
existing startup / shutdown / bind-failure / configuration-fallback seams, and
the guarantee that private message content and credential material never reach
ordinary diagnostics.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.server.config import ServiceConfig  # noqa: E402
from danmaku.server.diagnostics import (  # noqa: E402
    ALLOWED_EVENTS,
    BACKUP_COUNT,
    LOG_FILENAME,
    MAX_LOG_BYTES,
    Diagnostics,
    default_log_path,
    redact,
)
from danmaku.server.runner import Service  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _read_records(*paths: Path) -> list[dict]:
    records: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def _make_message(sequence: int, kind: str = "danmaku") -> object:
    from danmaku.core.model import Message

    data = {
        "danmaku": {"text": "PRIVATE_DANMAKU_TEXT_9841"},
        "gift": {"giftName": "Star", "quantity": 1, "totalAmountMilliCny": 1000},
        "guard": {"tier": "captain", "months": 1},
        "superChat": {
            "text": "PRIVATE_SUPERCHAT_TEXT_5522",
            "amountMilliCny": 30000,
            "durationSeconds": 60,
        },
    }[kind]
    return Message.from_dict(
        {
            "id": f"diag:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": "user:private-id", "name": "PrivateUserName"},
            "data": data,
        }
    )


class DefaultPolicyTests(unittest.TestCase):
    def test_max_log_bytes_is_canonical_10_mib(self):
        self.assertEqual(MAX_LOG_BYTES, 10 * 1024 * 1024)

    def test_backup_count_is_five(self):
        self.assertEqual(BACKUP_COUNT, 5)

    def test_default_log_path_is_next_to_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}, clear=True):
                path = default_log_path()
        self.assertEqual(
            path, Path(tmp) / ".config" / "danmaku" / LOG_FILENAME
        )

    def test_default_log_path_respects_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom" / "cfg.json"
            with mock.patch.dict(os.environ, {"DANMAKU_CONFIG": str(target)}, clear=True):
                path = default_log_path()
        self.assertEqual(path, Path(tmp) / "custom" / LOG_FILENAME)


class AllowListTests(unittest.TestCase):
    def test_allowed_events_are_operational_only(self):
        self.assertEqual(
            ALLOWED_EVENTS, {"startup", "shutdown", "config_fallback", "bind_failure"}
        )

    def test_unknown_event_kind_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnostics = Diagnostics(Path(tmp) / "danmaku.log")
            with self.assertRaises(ValueError):
                diagnostics.record("message", text="hello")

    def test_unknown_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnostics = Diagnostics(Path(tmp) / "danmaku.log")
            with self.assertRaises(ValueError):
                diagnostics.record(
                    "startup", host="127.0.0.1", port=17391, text="hello"
                )

    def test_shutdown_accepts_no_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "danmaku.log"
            diagnostics = Diagnostics(path)
            diagnostics.record("shutdown")
            records = _read_records(path)
            self.assertEqual(records[0]["event"], "shutdown")
            self.assertEqual(set(records[0]), {"event", "timestamp"})


class RedactionTests(unittest.TestCase):
    _SECRET_MARKERS = (
        "accesskeysecret",
        "access_key_secret",
        "identitycode",
        "identity_code",
        "sessdata",
        "bili_jct",
        "csrf",
        "buvid",
        "cookie",
        "token",
        "credential",
        "password",
        "authorization",
        "secretkey",
        "apisecret",
    )

    def test_redact_returns_fixed_placeholder(self):
        self.assertEqual(redact("hunter2"), "<redacted>")

    def test_opaque_values_are_redacted_not_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "danmaku.log"
            diagnostics = Diagnostics(path)
            diagnostics.record(
                "config_fallback", source={"secret": "hunter2"}, reason="ok"
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("<redacted>", text)
        self.assertNotIn("hunter2", text)
        self.assertNotIn("secret", text)

    def test_long_string_is_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "danmaku.log"
            diagnostics = Diagnostics(path)
            diagnostics.record(
                "config_fallback", source="defaults", reason="x" * 1000
            )
            record = _read_records(path)[0]
        self.assertEqual(len(record["reason"]), 256)

    def test_records_are_single_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "danmaku.log"
            diagnostics = Diagnostics(path)
            diagnostics.record("startup", host="127.0.0.1", port=17391)
            diagnostics.record("shutdown")
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertIsInstance(json.loads(line), dict)

    def test_no_secret_markers_in_ordinary_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "danmaku.log"
            diagnostics = Diagnostics(path)
            diagnostics.record("startup", host="127.0.0.1", port=17391)
            diagnostics.record(
                "config_fallback",
                source="defaults",
                reason="primary config is invalid; starting with safe defaults",
            )
            diagnostics.record("bind_failure", host="127.0.0.1", port=17391)
            diagnostics.record("shutdown")
            text = path.read_text(encoding="utf-8").lower()
        for marker in self._SECRET_MARKERS:
            self.assertNotIn(marker, text)


class RotationTests(unittest.TestCase):
    def test_active_log_rotates_and_caps_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "danmaku.log"
            diagnostics = Diagnostics(path, max_bytes=200, backup_count=2)
            for index in range(60):
                diagnostics.record(
                    "config_fallback",
                    source="defaults",
                    reason=f"fallback-{index:02d}",
                )
            diagnostics.close()

            self.assertTrue(path.exists())
            backups = sorted(Path(tmp).glob("danmaku.log.*"))
            self.assertLessEqual(len(backups), 2)
            self.assertFalse((Path(tmp) / "danmaku.log.3").exists())
            for candidate in [path, *backups]:
                self.assertLessEqual(candidate.stat().st_size, 200 + 400)
            self.assertIn("fallback-59", path.read_text(encoding="utf-8"))

    def test_backup_count_zero_truncates_and_retains_no_backups(self):
        """Regression: ``backup_count=0`` must truncate, never grow backups.

        A plain append-mode rollover would leave the active log growing without
        bound while still creating numbered backups. The supported behavior is
        explicit, deterministic, and bounded: the active log is truncated in
        place and no ``.N`` backup is ever retained.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "danmaku.log"
            diagnostics = Diagnostics(path, max_bytes=200, backup_count=0)
            for index in range(60):
                diagnostics.record(
                    "config_fallback",
                    source="defaults",
                    reason=f"fallback-{index:02d}",
                )
            diagnostics.close()

            files = sorted(Path(tmp).glob("danmaku.log*"))
            self.assertEqual(files, [path])
            self.assertLessEqual(path.stat().st_size, 200 + 400)
            self.assertIn("fallback-59", path.read_text(encoding="utf-8"))

    def test_constructor_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            Diagnostics(Path("x.log"), max_bytes=0)
        with self.assertRaises(ValueError):
            Diagnostics(Path("x.log"), backup_count=-1)

    def test_disabled_diagnostics_writes_nothing(self):
        diagnostics = Diagnostics(None)
        self.assertFalse(diagnostics.enabled)
        self.assertIsNone(diagnostics.path)
        diagnostics.record("startup", host="127.0.0.1", port=17391)
        diagnostics.record("shutdown")
        diagnostics.close()


class ServiceDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self.tmp.name) / "danmaku.log"

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def _diagnostics(self):
        return Diagnostics(self.log_path)

    def _service(self, port, diagnostics):
        return Service(
            ServiceConfig(port=port, cadence_milliseconds=60000),
            diagnostics=diagnostics,
        )

    async def test_startup_and_shutdown_are_recorded(self):
        service = self._service(_free_port(), self._diagnostics())
        await service.start()
        await service.stop()
        records = _read_records(self.log_path)
        self.assertEqual([r["event"] for r in records], ["startup", "shutdown"])
        self.assertEqual(records[0]["host"], "127.0.0.1")
        self.assertEqual(records[0]["port"], service.port)

    async def test_bind_failure_is_recorded(self):
        port = _free_port()
        first = Service(ServiceConfig(port=port, cadence_milliseconds=60000))
        await first.start()
        try:
            second = self._service(port, self._diagnostics())
            with self.assertRaises(OSError):
                await second.start()
            records = _read_records(self.log_path)
            self.assertEqual([r["event"] for r in records], ["bind_failure"])
            self.assertEqual(records[0]["host"], "127.0.0.1")
            self.assertEqual(records[0]["port"], port)
        finally:
            await first.stop()

    async def test_message_content_never_recorded(self):
        service = self._service(_free_port(), self._diagnostics())
        await service.start()
        try:
            # Let the producer emit its first danmaku so later publishes keep
            # monotonically increasing sequence order.
            for _ in range(200):
                if len(service.hub.snapshot()) >= 1:
                    break
                await asyncio.sleep(0.005)
            service.hub.publish(_make_message(100, "danmaku"))
            service.hub.publish(_make_message(101, "superChat"))
        finally:
            await service.stop()

        text = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_DANMAKU_TEXT_9841", text)
        self.assertNotIn("PRIVATE_SUPERCHAT_TEXT_5522", text)
        self.assertNotIn("PrivateUserName", text)
        self.assertNotIn("user:private-id", text)
        # The producer's own mock message content is also never recorded.
        self.assertNotIn("欢迎来到", text)
        self.assertNotIn("user:alice", text)
        self.assertNotIn("Alice", text)


if __name__ == "__main__":
    unittest.main()
