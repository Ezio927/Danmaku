import os
import unittest

from danmaku_bot.config import BotConfig, load_config


class LoadConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._old_env = dict(os.environ)
        for key in ("DANMAKU_ROOM_ID", "DANMAKU_PLATFORM", "DANMAKU_COMMAND_PREFIX"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_load_config_requires_room_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "DANMAKU_ROOM_ID is required"):
            load_config()

    def test_load_config_reads_environment(self) -> None:
        os.environ["DANMAKU_ROOM_ID"] = "10086"
        os.environ["DANMAKU_PLATFORM"] = "mock"
        os.environ["DANMAKU_COMMAND_PREFIX"] = "#"

        config = load_config()

        self.assertEqual(BotConfig(room_id="10086", platform="mock", command_prefix="#"), config)

