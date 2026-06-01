from collections.abc import Callable

from .client import DanmakuClient, DanmakuMessage
from .config import BotConfig


CommandHandler = Callable[[DanmakuMessage], str]


class DanmakuBot:
    def __init__(self, config: BotConfig, client: DanmakuClient):
        self._config = config
        self._client = client
        self._handlers: dict[str, CommandHandler] = {
            "ping": lambda _: "pong",
            "help": lambda _: "可用命令: ping, help",
        }

    def register_command(self, command: str, handler: CommandHandler) -> None:
        self._handlers[command.strip().lower()] = handler

    def run(self) -> None:
        self._client.connect()
        for message in self._client.receive_messages():
            reply = self._handle_message(message)
            if reply:
                self._client.send_message(reply)

    def _handle_message(self, message: DanmakuMessage) -> str | None:
        prefix = self._config.command_prefix
        if not message.content.startswith(prefix):
            return None

        command_text = message.content[len(prefix) :].strip()
        if not command_text:
            return None

        command = command_text.split()[0].lower()
        handler = self._handlers.get(command)
        if handler is None:
            return f"未知命令: {command}"
        return handler(message)

