import unittest

from danmaku_bot.bot import DanmakuBot
from danmaku_bot.client import DanmakuMessage, MockDanmakuClient
from danmaku_bot.config import BotConfig


class DanmakuBotTestCase(unittest.TestCase):
    def test_bot_handles_known_and_unknown_commands(self) -> None:
        client = MockDanmakuClient(
            scripted_messages=[
                DanmakuMessage(user="alice", content="!ping"),
                DanmakuMessage(user="bob", content="!missing"),
                DanmakuMessage(user="eve", content="hello"),
            ]
        )
        bot = DanmakuBot(config=BotConfig(room_id="1"), client=client)

        bot.run()

        self.assertTrue(client.connected)
        self.assertEqual(["pong", "未知命令: missing"], client.sent_messages)

    def test_register_command(self) -> None:
        client = MockDanmakuClient(
            scripted_messages=[DanmakuMessage(user="alice", content="!hi")]
        )
        bot = DanmakuBot(config=BotConfig(room_id="1"), client=client)
        bot.register_command("hi", lambda message: f"你好, {message.user}")

        bot.run()

        self.assertEqual(["你好, alice"], client.sent_messages)

