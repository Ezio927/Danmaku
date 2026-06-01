from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass(frozen=True)
class DanmakuMessage:
    user: str
    content: str


class DanmakuClient(Protocol):
    def connect(self) -> None:
        ...

    def receive_messages(self) -> Iterable[DanmakuMessage]:
        ...

    def send_message(self, content: str) -> None:
        ...


class MockDanmakuClient:
    def __init__(self, scripted_messages: Iterable[DanmakuMessage] | None = None):
        self._messages = list(scripted_messages or [])
        self.sent_messages: list[str] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def receive_messages(self) -> Iterable[DanmakuMessage]:
        return list(self._messages)

    def send_message(self, content: str) -> None:
        self.sent_messages.append(content)

