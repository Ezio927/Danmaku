from dataclasses import dataclass
import os


@dataclass(frozen=True)
class BotConfig:
    room_id: str
    platform: str = "mock"
    command_prefix: str = "!"


def load_config() -> BotConfig:
    room_id = os.getenv("DANMAKU_ROOM_ID", "").strip()
    if not room_id:
        raise ValueError("DANMAKU_ROOM_ID is required")

    platform = os.getenv("DANMAKU_PLATFORM", "mock").strip() or "mock"
    command_prefix = os.getenv("DANMAKU_COMMAND_PREFIX", "!").strip() or "!"
    return BotConfig(
        room_id=room_id,
        platform=platform,
        command_prefix=command_prefix,
    )

