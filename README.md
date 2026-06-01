# 直播弹幕姬（Python）项目框架

这是一个**最小可扩展**的弹幕姬项目框架，适合后续接入 B 站、斗鱼、虎牙等平台的弹幕接口。

## 项目结构

```text
danmaku_bot/
  __init__.py
  config.py           # 配置加载
  client.py           # 弹幕客户端抽象与 Mock 客户端
  bot.py              # 机器人核心逻辑（命令路由）
  main.py             # 启动入口
tests/
  test_bot.py
  test_config.py
```

## 快速开始

1. 配置环境变量（示例）：

```bash
export DANMAKU_ROOM_ID="123456"
export DANMAKU_PLATFORM="mock"
export DANMAKU_COMMAND_PREFIX="!"
```

2. 运行：

```bash
python -m danmaku_bot.main
```

3. 运行测试：

```bash
python -m unittest discover -v
```

## 后续扩展建议

- 在 `client.py` 中新增真实平台客户端（WebSocket/HTTP API）。
- 在 `bot.py` 里注册更多业务命令（点歌、抽奖、关键词回复等）。
- 增加持久化模块（黑名单、用户积分、礼物统计）。