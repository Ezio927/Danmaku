from .bot import DanmakuBot
from .client import MockDanmakuClient
from .config import load_config


def main() -> None:
    config = load_config()
    client = MockDanmakuClient()
    bot = DanmakuBot(config=config, client=client)
    bot.run()


if __name__ == "__main__":
    main()

