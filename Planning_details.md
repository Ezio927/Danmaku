# Bilibili 直播弹幕机 — 纯 Python 应用程序分析

## Context

基于 [Danmaku/docs/](Danmaku/docs/) 下三份文档（需求分析、开发前设计清单、CI测试策略），分析用纯 Python 实现该弹幕机应用所需的：函数/类、库/方法、目录结构、项目框架。

---

## 1. 整体项目框架

```
┌──────────────────────────────────────────────────┐
│                   Windows 10/11 x64               │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │  主播窗口     │  │  OBS 页面     │  │ 系统托盘  │ │
│  │ (Tkinter)   │  │ (HTML/HTTP)  │  │ (pystray)│ │
│  └──────┬──────┘  └──────┬───────┘  └────┬─────┘ │
│         │                │               │        │
│  ┌──────┴────────────────┴───────────────┴─────┐  │
│  │            本地 HTTP/WebSocket 服务           │  │
│  │         (Flask / aiohttp / FastAPI)          │  │
│  └──────┬──────────────────────────────────────┘  │
│         │                                         │
│  ┌──────┴──────────────────────────────────────┐  │
│  │              核心引擎层                       │  │
│  │  ┌──────────┐ ┌────────┐ ┌───────────────┐ │  │
│  │  │消息标准化  │ │礼物合并 │ │ OBS 过滤引擎  │ │  │
│  │  └──────────┘ └────────┘ └───────────────┘ │  │
│  │  ┌──────────┐ ┌────────┐ ┌───────────────┐ │  │
│  │  │醒目留言   │ │消息缓冲 │ │ 高流量保护     │ │  │
│  │  │管理器     │ │(1000条)│ │               │ │  │
│  │  └──────────┘ └────────┘ └───────────────┘ │  │
│  └──────┬──────────────────────────────────────┘  │
│         │                                         │
│  ┌──────┴──────────────────────────────────────┐  │
│  │          Bilibili 开放平台适配器              │  │
│  │       (WebSocket 连接/心跳/重连)              │  │
│  └──────┬──────────────────────────────────────┘  │
│         │                                         │
│  ┌──────┴──────────┐  ┌──────────────────────┐   │
│  │ 配置管理         │  │ 诊断/日志             │   │
│  │ (JSON+DPAPI)    │  │ (logging轮转)         │   │
│  └─────────────────┘  └──────────────────────┘   │
└──────────────────────────────────────────────────┘
```

**进程模型**：单进程多线程。主线程运行 GUI，后台线程处理 WebSocket 连接和 HTTP 服务。

---

## 2. 目录结构

```
Danmaku/
├── src/
│   ├── __init__.py
│   ├── main.py                    # 应用程序入口，单实例锁
│   ├── app.py                     # 应用程序主控，生命周期管理
│   │
│   ├── gui/                       # 主播窗口（Tkinter 实现）
│   │   ├── __init__.py
│   │   ├── main_window.py         # 主窗口（紧凑单列）
│   │   ├── timeline.py            # 消息时间线（虚拟列表）
│   │   ├── pinned_area.py         # 醒目留言置顶展开区
│   │   ├── status_bar.py          # 三层状态栏（Bilibili/本地/OBS）
│   │   ├── tray.py                # 系统托盘
│   │   ├── dialogs/               # 对话框
│   │   │   ├── __init__.py
│   │   │   ├── config_wizard.py   # 首次配置向导
│   │   │   ├── identity_code.py   # 身份码输入
│   │   │   ├── settings.py        # 设置对话框
│   │   │   ├── obs_filter.py      # OBS 过滤设置
│   │   │   ├── obs_style.py       # OBS 样式设置
│   │   │   └── diagnostics.py     # 诊断信息
│   │   └── context_menu.py        # 消息右键菜单
│   │
│   ├── server/                    # 本地 HTTP/WebSocket 服务
│   │   ├── __init__.py
│   │   ├── http_server.py         # HTTP 服务启动/停止
│   │   ├── ws_handler.py          # WebSocket 连接管理
│   │   ├── routes.py              # API 路由
│   │   └── obs_page/              # OBS 页面静态资源
│   │       ├── index.html         # OBS 透明页面
│   │       ├── style.css          # YouTube 经典主题
│   │       └── app.js             # OBS 前端逻辑
│   │
│   ├── core/                      # 核心引擎
│   │   ├── __init__.py
│   │   ├── event_bus.py           # 内部事件总线
│   │   ├── message.py             # 统一消息模型
│   │   ├── message_buffer.py      # 主播回看缓冲（1000条）
│   │   ├── gift_merger.py         # 礼物连送合并
│   │   ├── obs_filter.py          # OBS 过滤引擎（用户/关键词/金额）
│   │   ├── obs_queue.py           # OBS 有界平滑队列
│   │   ├── pinned_manager.py      # 醒目留言置顶管理
│   │   └── snapshot.py            # OBS 快照管理
│   │
│   ├── adapter/                   # Bilibili 开放平台适配器
│   │   ├── __init__.py
│   │   ├── bilibili_client.py     # WebSocket 客户端
│   │   ├── auth.py                # 凭证与场次管理
│   │   ├── normalizer.py          # 原始事件→内部消息标准化
│   │   └── protocol.py            # Bilibili 协议定义
│   │
│   ├── config/                    # 配置管理
│   │   ├── __init__.py
│   │   ├── config_manager.py      # 配置读写/校验/备份/迁移
│   │   ├── schema.py              # 配置结构定义
│   │   └── secret_store.py        # Windows 安全凭据存储
│   │
│   ├── diagnostics/               # 诊断模块
│   │   ├── __init__.py
│   │   ├── logger.py              # 日志配置（10MB×5轮转）
│   │   ├── diagnostic_pack.py     # 脱敏诊断包生成
│   │   └── mock_events.py         # 模拟消息生成器
│   │
│   └── utils/                     # 工具模块
│       ├── __init__.py
│       ├── singleton.py           # 单实例检测
│       ├── text_filter.py         # 关键词匹配（不区分大小写）
│       └── helpers.py             # 通用工具函数
│
├── tests/                         # 测试目录
│   ├── __init__.py
│   ├── test_normalizer.py         # 消息标准化测试
│   ├── test_obs_filter.py         # OBS 过滤测试
│   ├── test_gift_merger.py        # 礼物合并测试
│   ├── test_pinned_manager.py     # 醒目留言测试
│   ├── test_message_buffer.py     # 回看缓冲测试
│   ├── test_snapshot.py           # 快照测试
│   ├── test_config.py             # 配置测试
│   ├── test_protocol.py           # 协议测试
│   ├── test_integration.py        # 集成测试
│   └── mock_server.py             # Mock Bilibili 消息源
│
├── docs/                          # 设计文档
│   ├── danmaku_requirements.md    # 需求分析（已存在）
│   ├── before_coding_checklist.md # 设计清单（已存在）
│   ├── ci_test_strategy.md        # CI测试策略（已存在）
│   ├── architecture.md            # 架构文档（待产出）
│   ├── detailed-design.md         # 详细设计（待产出）
│   ├── message-model.md           # 消息模型（待产出）
│   ├── api-protocol.md            # API协议（待产出）
│   ├── configuration-schema.md    # 配置设计（待产出）
│   ├── ui-wireframes.md           # 界面线框图（待产出）
│   ├── test-plan.md               # 测试计划（待产出）
│   └── adr/                       # 架构决策记录（待产出）
│
├── requirements.txt               # 运行时依赖
├── requirements-dev.txt           # 开发依赖
├── setup.py / pyproject.toml      # 打包配置
├── Makefile                       # 常用命令
├── .gitignore
├── .editorconfig                  #（已存在）
├── LICENSE                        #（已存在）
└── README.md
```

---

## 3. 用到的库和方法

### 3.1 GUI（主播窗口）
| 库/模块 | 用途 | 关键方法/类 |
|----------|------|-------------|
| `tkinter` | 主窗口框架 | `Tk`, `Frame`, `Canvas`, `Scrollbar`, `Menu`, `Toplevel` |
| `tkinter.ttk` | 主题化组件 | `Treeview`（虚拟列表思路）, `Notebook`, `Button`, `Label` |
| `pystray` | 系统托盘 | `Icon`, `MenuItem`, `run_detached` |
| `PIL.Image/ImageDraw` | 托盘图标绘制 | `Image.new`, `ImageDraw.Draw` |

### 3.2 HTTP/WebSocket 服务
| 库/模块 | 用途 | 关键方法/类 |
|----------|------|-------------|
| `aiohttp` 或 `flask` + `flask-sock` | HTTP + WebSocket 服务 | 路由注册、WebSocket 端点、静态文件服务 |
| `asyncio` | 异步 IO | `create_task`, `Queue`, `Event`, `sleep`, `run` |

### 3.3 Bilibili 连接
| 库/模块 | 用途 | 关键方法/类 |
|----------|------|-------------|
| `websockets` 或 `aiohttp` | WebSocket 客户端 | `connect`, `send`, `recv`, `ping/pong` |
| `requests` / `httpx` | HTTP API 调用 | `post`, `get`（场次启动/结束） |
| `asyncio` | 心跳定时器、重连退避 | `create_task`, `sleep` |

### 3.4 配置与安全
| 库/模块 | 用途 | 关键方法/类 |
|----------|------|-------------|
| `json` | JSON 配置文件读写 | `load`, `dump`, `JSONDecodeError` |
| `dataclasses` / `pydantic` | 配置模型定义 | `dataclass`, `field` 或 `BaseModel` |
| `keyring` 或 `win32crypt` | Windows 凭据安全存储 | `set_password`, `get_password`, `delete_password` |
| `pathlib` | 跨平台路径处理 | `Path.mkdir(parents=True)`, `Path.read_text` |

### 3.5 日志与诊断
| 库/模块 | 用途 | 关键方法/类 |
|----------|------|-------------|
| `logging` | 日志系统 | `RotatingFileHandler`, `Logger`, `Formatter` |
| `logging.handlers.RotatingFileHandler` | 日志轮转（10MB×5） | `maxBytes=10*1024*1024`, `backupCount=5` |
| `zipfile` / `tarfile` | 诊断包打包 | `ZipFile` |
| `platform` | 系统信息 | `system()`, `version()`, `architecture()` |

### 3.6 测试
| 库/模块 | 用途 | 关键方法/类 |
|----------|------|-------------|
| `unittest` | 单元测试框架 | `TestCase`, `mock.Mock`, `mock.patch` |
| `pytest`（推荐） | 增强测试 | `fixture`, `mark.parametrize`, `raises` |
| `asyncio` 测试 | 异步测试 | `pytest-asyncio` 或 `unittest.IsolatedAsyncioTestCase` |

### 3.7 打包与分发
| 库/模块 | 用途 | 关键方法/类 |
|----------|------|-------------|
| `PyInstaller` 或 `cx_Freeze` | 打包为 Windows 安装包 | 打包为 .exe，包含所有依赖 |
| `NSIS` 或 `Inno Setup` | Windows 安装程序制作 | 创建标准安装包 |

---

## 4. 核心类与函数（中文描述）

### 4.1 `src/main.py` — 应用程序入口

#### 函数：
| 函数名 | 描述 |
|--------|------|
| `main()` | 应用程序入口，解析命令行参数，检查单实例锁，创建 Application 实例并启动 |
| `check_single_instance()` | 使用文件锁/命名管道检测是否已有实例运行，如有则唤醒已有窗口并退出 |

---

### 4.2 `src/app.py` — `Application` 类

#### 类：`Application`（应用程序主控）

**职责**：管理整个应用的生命周期，协调各子系统启动/停止

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `config_manager` | 配置管理器实例，负责配置的读写和校验 |
| `event_bus` | 内部事件总线，各模块通过它解耦通信 |
| `bilibili_client` | Bilibili WebSocket 客户端适配器 |
| `http_server` | 本地 HTTP/WebSocket 服务实例 |
| `message_buffer` | 主播回看消息缓冲（最近 1000 条） |
| `gift_merger` | 礼物连送合并器 |
| `obs_filter` | OBS 过滤引擎 |
| `obs_queue` | OBS 平滑队列 |
| `pinned_manager` | 醒目留言置顶管理器 |
| `snapshot_manager` | OBS 快照管理器 |
| `main_window` | 主播窗口引用 |
| `tray_icon` | 系统托盘图标 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `start()` | 启动所有子系统：加载配置、初始化事件总线、启动 HTTP 服务、显示主窗口、设置系统托盘 |
| `shutdown()` | 正常关闭：停止 Bilibili 连接、结束场次、保存配置、停止 HTTP 服务、清理资源 |
| `connect_bilibili(identity_code)` | 使用身份码连接到 Bilibili 开放平台 |
| `disconnect_bilibili()` | 断开 Bilibili 连接并结束场次 |
| `clear_obs()` | 清空 OBS 画面（保留有效醒目留言） |
| `clear_obs_all()` | 清空 OBS 画面（含醒目留言） |
| `get_diagnostic_pack()` | 生成脱敏诊断包 |

---

### 4.3 `src/gui/main_window.py` — `MainWindow` 类

#### 类：`MainWindow`（主播紧凑窗口）

**职责**：主播监看弹幕的主窗口，包含状态栏、醒目留言置顶区、消息时间线

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `root` | Tkinter 根窗口对象 |
| `status_bar` | 三层状态栏组件（Bilibili/本地/OBS） |
| `pinned_area` | 醒目留言置顶展开区组件 |
| `timeline` | 消息时间线组件（虚拟列表） |
| `is_paused` | 是否暂停跟随最新消息 |
| `pending_count` | 暂停期间新增消息数量 |
| `window_position` | 窗口位置记忆 |
| `window_size` | 窗口尺寸记忆 |
| `always_on_top` | 是否始终置顶 |
| `opacity` | 窗口透明度 |
| `close_behavior` | 关闭按钮行为（隐藏托盘/直接退出/每次询问） |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `show()` | 显示窗口并恢复到记忆的位置和尺寸 |
| `hide_to_tray()` | 隐藏窗口到系统托盘 |
| `on_close()` | 关闭按钮点击处理，根据 close_behavior 决定行为 |
| `on_message(msg)` | 接收到新消息，加入时间线，更新置顶区 |
| `pause_follow()` | 暂停跟随最新消息（向上滚动触发） |
| `resume_follow()` | 恢复跟随最新消息（滚到底部或点击提示） |
| `clear_view()` | 清空当前视图（不清空缓冲） |
| `save_window_state()` | 保存窗口位置、尺寸、置顶和透明度到配置 |
| `restore_window_state()` | 从配置恢复窗口状态 |

---

### 4.4 `src/gui/timeline.py` — `Timeline` 类

#### 类：`Timeline`（消息时间线）

**职责**：使用虚拟列表方式高效渲染消息流，支持滚动暂停和恢复

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `canvas` | Tkinter Canvas 控件，用于绘制消息 |
| `scrollbar` | 滚动条 |
| `visible_range` | 当前可见的消息索引范围 |
| `scroll_position` | 当前滚动位置 |
| `is_paused` | 是否暂停跟随 |
| `pending_count` | 暂停期间未读消息数量 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `add_message(msg)` | 添加一条消息到时间线，如未暂停则自动滚动到底部 |
| `render_visible()` | 只渲染当前可见区域的消息（虚拟列表核心） |
| `on_scroll(event)` | 滚动事件处理，检测是否暂停/恢复跟随 |
| `scroll_to_bottom()` | 滚动到最底部，恢复跟随 |
| `clear()` | 清空时间线显示 |
| `show_context_menu(x, y, msg)` | 在指定位置显示消息右键菜单 |

---

### 4.5 `src/gui/pinned_area.py` — `PinnedArea` 类

#### 类：`PinnedArea`（醒目留言置顶展开区）

**职责**：在主窗口顶部展开显示醒目的留言内容，支持手动切换和自动标记已展示

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `current_pinned` | 当前正在展示的醒目留言 |
| `pending_queue` | 待展示的醒目留言队列（按时间排序） |
| `display_timer` | 当前展示计时器（3秒后标记已展示） |
| `unviewed_count` | 尚未展示的醒目留言数量 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `add_pinned(msg)` | 新醒目留言入队，不打断当前正在展示的内容 |
| `show_next()` | 手动查看下一条醒目留言 |
| `show_prev()` | 手动查看上一条醒目留言 |
| `on_display_timeout()` | 展示达到 3 秒后标记为"已展示" |
| `remove_pinned(msg_id)` | 收到删除事件后移除指定醒目留言 |
| `on_expire(msg_id)` | 置顶到期：退出顶部展开区，保留时间线记录 |

---

### 4.6 `src/gui/status_bar.py` — `StatusBar` 类

#### 类：`StatusBar`（三层状态栏）

**职责**：分别显示 Bilibili 连接状态、本地服务状态、OBS 连接数

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `bilibili_status` | Bilibili 连接状态：未配置/等待身份码/启动场次/连接中/已连接/重连中 |
| `local_status` | 本地 HTTP 服务状态：运行中/已停止/端口冲突 |
| `obs_count` | 当前连接的 OBS 来源数量 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `set_bilibili_status(status)` | 更新 Bilibili 连接状态显示 |
| `set_local_status(status)` | 更新本地服务状态显示 |
| `set_obs_count(count)` | 更新 OBS 连接数显示 |

---

### 4.7 `src/gui/tray.py` — `SystemTray` 类

#### 类：`SystemTray`（系统托盘）

**职责**：管理系统托盘图标和菜单

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `icon` | pystray Icon 实例 |
| `menu_items` | 托盘菜单项字典 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `setup()` | 创建托盘图标和菜单（显示窗口/停止连接/设置/退出） |
| `show_window()` | 显示主播窗口 |
| `toggle_connection()` | 切换连接/断开状态 |
| `open_settings()` | 打开设置对话框 |
| `quit_app()` | 退出应用程序 |
| `show_notification(title, msg)` | 显示托盘通知气泡 |

---

### 4.8 `src/core/message.py` — 消息模型

#### 类：`MessageType`（消息类型枚举）
- `DANMAKU` — 普通弹幕
- `EMOTION` — Bilibili 表情弹幕
- `GIFT` — 付费礼物
- `GUARD` — 上舰
- `SUPER_CHAT` — 醒目留言
- `SUPER_CHAT_DELETE` — 醒目留言删除
- `SYSTEM` — 系统消息

#### 类：`UnifiedMessage`（统一消息模型）

**职责**：所有消息的统一数据结构，屏蔽平台差异

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `msg_id` | 消息唯一标识 |
| `msg_type` | 消息类型（MessageType 枚举） |
| `received_at` | 本地接收时间 |
| `platform_time` | 平台原始时间戳 |
| `user_id` | 用户稳定标识（平台唯一 ID） |
| `user_name` | 用户昵称 |
| `user_avatar` | 用户头像 URL |
| `user_identity` | 用户身份（普通用户/舰队/房管/主播） |
| `fans_medal` | 粉丝牌信息（等级、名称） |
| `guard_level` | 舰队等级（舰长/提督/总督） |
| `content` | 消息正文（弹幕/表情/醒目留言正文） |
| `gift_id` | 礼物 ID（仅礼物消息） |
| `gift_name` | 礼物名称 |
| `gift_count` | 礼物数量 |
| `gift_amount` | 礼物金额（元） |
| `sc_duration` | 醒目留言有效期（秒） |
| `sc_amount` | 醒目留言金额 |
| `sc_color` | 醒目留言档位颜色 |
| `raw_event` | 原始事件引用（用于诊断和扩展） |

#### 类：`GiftAggregate`（礼物聚合消息）

**职责**：合并连送礼物后的聚合表示

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `base_msg` | 基础消息信息 |
| `total_count` | 累计礼物数量 |
| `total_amount` | 累计礼物金额 |
| `last_update` | 最后一次收到同类礼物的时间 |
| `is_combined` | 是否平台确认的连送/组合 |

---

### 4.9 `src/core/event_bus.py` — `EventBus` 类

#### 类：`EventBus`（内部事件总线）

**职责**：模块间解耦通信，支持同步/异步事件发布订阅

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `subscribers` | 事件类型→订阅者列表的映射字典 |
| `message_queue` | 内部消息队列（用于高流量缓冲） |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `subscribe(event_type, callback)` | 订阅某类事件 |
| `unsubscribe(event_type, callback)` | 取消订阅 |
| `publish(event_type, data)` | 发布事件到所有订阅者 |
| `publish_async(event_type, data)` | 异步发布（非阻塞） |

**事件类型**：
- `message.received` — 收到新消息
- `message.deleted` — 消息被删除
- `connection.state_changed` — 连接状态变更
- `obs.client_connected` — OBS 来源连接
- `obs.client_disconnected` — OBS 来源断开
- `config.changed` — 配置变更
- `app.shutdown` — 应用关闭

---

### 4.10 `src/core/message_buffer.py` — `MessageBuffer` 类

#### 类：`MessageBuffer`（主播回看缓冲）

**职责**：内存中保留最近 1000 条消息，支持滚动回看，超过上限淘汰最旧消息

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `messages` | 消息列表（deque，最大 1000 条） |
| `max_size` | 最大容量（1000） |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `add(msg)` | 添加消息，超过 max_size 时自动淘汰最旧消息 |
| `get_recent(n)` | 获取最近 n 条消息 |
| `get_range(start, end)` | 获取指定范围的消息 |
| `get_by_id(msg_id)` | 按消息 ID 查找消息 |
| `mark_deleted(msg_id)` | 将消息标记为灰色"已被删除" |
| `clear()` | 清空缓冲区 |

---

### 4.11 `src/core/gift_merger.py` — `GiftMerger` 类

#### 类：`GiftMerger`（礼物连送合并器）

**职责**：合并同一用户的连送礼物，优先使用平台标识，备用 5 秒滑动窗口

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `active_groups` | 当前活跃的礼物聚合组（房间+用户+礼物ID → GiftAggregate） |
| `merge_window` | 备用合并窗口（5 秒） |
| `merge_timer` | 清理过期聚合组的定时器 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `process_gift(msg)` | 处理一条礼物消息：有平台连送标识则直接合并，无标识则用 5 秒窗口匹配 |
| `get_key(msg)` | 生成合并键（房间+用户ID+礼物ID） |
| `should_merge(existing, new)` | 判断两条礼物是否应该合并（用户/礼物相同、时间在窗口内） |
| `cleanup_expired()` | 清理超出合并窗口的过期聚合组 |
| `flush_group(key)` | 强制结束指定聚合组，产出最终 GiftAggregate |

---

### 4.12 `src/core/obs_filter.py` — `ObsFilter` 类

#### 类：`ObsFilter`（OBS 过滤引擎）

**职责**：在消息分发给 OBS 前执行用户屏蔽、关键词过滤和金额阈值过滤

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `blocked_users` | 被屏蔽的用户 ID 集合（优先稳定 ID，无则用昵称） |
| `blocked_nicknames` | 被屏蔽的用户昵称集合（退化方案） |
| `keywords` | 关键词列表（去重、去首尾空格） |
| `gift_threshold` | 礼物金额阈值（默认 0.1 元） |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `should_block(msg)` | 判断消息是否应被 OBS 过滤（综合用户/关键词/金额） |
| `is_user_blocked(msg)` | 检查消息发送者是否在屏蔽名单中 |
| `is_keyword_match(msg)` | 检查消息正文是否包含关键词（只匹配弹幕/醒目留言正文，不区分大小写） |
| `is_gift_below_threshold(msg)` | 检查付费礼物金额是否低于阈值 |
| `add_blocked_user(user_id, nickname)` | 添加用户到屏蔽名单 |
| `remove_blocked_user(user_id)` | 从屏蔽名单移除用户 |
| `add_keyword(keyword)` | 添加关键词 |
| `remove_keyword(keyword)` | 移除关键词 |
| `set_gift_threshold(amount)` | 设置礼物金额阈值 |
| `test_text(text)` | 测试文本是否命中关键词（供设置界面测试功能） |
| `apply_existing_rules_to_obs()` | 新增规则后，移除 OBS 当前可见的匹配内容 |
| `export_rules()` | 导出过滤规则 |
| `import_rules(rules)` | 导入过滤规则 |

---

### 4.13 `src/core/obs_queue.py` — `ObsQueue` 类

#### 类：`ObsQueue`（OBS 有界平滑队列）

**职责**：管理 OBS 弹幕显示节奏，正常逐条显示，高流量缩短间隔或批量推进

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `queue` | 待展示消息队列（asyncio.Queue，最多 200 条） |
| `normal_interval` | 正常动画间隔（约 200ms） |
| `fast_interval` | 高流量最快间隔（约 80ms） |
| `max_queue_size` | 队列上限（200 条或预计延迟约 5 秒） |
| `is_running` | 队列是否正在处理 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `enqueue(msg)` | 将消息加入队列，超限时淘汰最旧未展示普通弹幕 |
| `process_loop()` | 异步循环：从队列取消息→通过 WebSocket 发送给 OBS→等待间隔 |
| `bypass_queue(msg)` | 付费互动（礼物/上舰/醒目留言）绕过队列立即发送 |
| `adjust_interval(queue_size)` | 根据当前队列长度动态调整发送间隔 |
| `get_queue_size()` | 获取当前队列长度 |
| `clear()` | 清空队列 |
| `get_stats()` | 获取队列统计信息（当前长度、平均延迟等） |

---

### 4.14 `src/core/pinned_manager.py` — `PinnedManager` 类

#### 类：`PinnedManager`（醒目留言管理器）

**职责**：管理醒目留言在主播侧和 OBS 侧的双端置顶、到期和删除行为

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `active_pinned` | 当前有效的醒目留言 dict（msg_id → SuperChatMsg） |
| `pinned_order` | 醒目留言的展示顺序（按时间） |
| `expire_timers` | 到期定时器 dict |
| `obs_pinned` | OBS 侧顶部摘要列表 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `add_super_chat(msg)` | 新增醒目留言：加入主播时间线排队，加入 OBS 顶部摘要 |
| `on_host_display_timeout(msg_id)` | 主播侧展示 3 秒后标记为"已展示" |
| `on_expire(msg_id)` | 醒目留言到期：退出主播顶部、退出 OBS 顶部摘要、保留时间线卡片 |
| `on_delete(msg_id)` | 收到删除：移除 OBS 卡片、顶部摘要和主播置顶，时间线留灰色记录 |
| `get_next_unviewed()` | 获取下一条尚未展示的醒目留言 |
| `get_prev_unviewed()` | 获取上一条尚未展示的醒目留言 |
| `get_obs_summary()` | 获取 OBS 顶部摘要列表（头像+金额+颜色+进度） |
| `get_all_active()` | 获取所有有效的醒目留言 |

---

### 4.15 `src/core/snapshot.py` — `SnapshotManager` 类

#### 类：`SnapshotManager`（OBS 快照管理器）

**职责**：维护 OBS 快照（最近 5 分钟、最多 100 条过滤后消息），供 OBS 重连时恢复

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `snapshot` | 当前快照（消息列表 + 醒目留言摘要 + 活跃礼物聚合） |
| `max_age` | 快照最大时间范围（5 分钟） |
| `max_count` | 快照最大消息数（100 条） |
| `active_sc` | 快照中仍有效的醒目留言 |
| `active_gifts` | 快照中未结束的礼物聚合状态 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `add_to_snapshot(msg)` | 将过滤后的消息加入快照 |
| `evict_expired()` | 淘汰超过 5 分钟的旧消息 |
| `evict_overflow()` | 超过 100 条时淘汰最旧消息 |
| `get_snapshot()` | 获取当前完整快照（供 OBS 重连） |
| `update_sc_state(sc_list)` | 更新醒目留言状态到快照 |
| `update_gift_state(gift_list)` | 更新礼物合并状态到快照 |
| `clear()` | 清空快照 |

---

### 4.16 `src/adapter/bilibili_client.py` — `BilibiliClient` 类

#### 类：`BilibiliClient`（Bilibili 客户端适配器）

**职责**：管理到 Bilibili 开放平台的 WebSocket 连接，处理心跳、断线重连

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `websocket` | WebSocket 连接对象 |
| `state` | 连接状态：未配置/等待身份码/启动场次/连接中/已连接/重连中 |
| `app_id` | 开放平台 App ID |
| `access_key_id` | Access Key ID |
| `access_key_secret` | Access Key Secret |
| `identity_code` | 身份码（仅内存，不跨重启保存） |
| `heartbeat_interval` | 心跳间隔 |
| `reconnect_delay` | 重连退避延迟 |
| `max_reconnect_delay` | 最大重连延迟 |
| `normalizer` | 消息标准化器实例 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `configure(app_id, key_id, key_secret)` | 配置开放平台凭证 |
| `set_identity_code(code)` | 设置身份码（仅内存） |
| `connect()` | 启动场次 → 建立 WebSocket 连接 → 开始心跳 |
| `disconnect()` | 断开 WebSocket → 结束场次 |
| `send_heartbeat()` | 发送心跳包 |
| `on_message(raw_data)` | 收到原始消息，交给 normalizer 标准化后发布到事件总线 |
| `on_disconnect(reason)` | 断开处理：网络错误自动退避重连，身份码失效停止重连 |
| `on_error(error)` | 错误分类：凭证错误/身份码错误/未开播/网络错误/平台异常 |
| `reconnect()` | 执行退避重连（指数退避） |
| `stop_reconnect()` | 停止重连（身份码失效等不可恢复错误） |
| `get_state()` | 获取当前连接状态 |

---

### 4.17 `src/adapter/auth.py` — `AuthManager` 类

#### 类：`AuthManager`（凭证与场次管理器）

**职责**：管理开放平台凭证验证和场次生命周期

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `app_id` | App ID |
| `access_key_id` | Access Key ID |
| `secret_store` | 安全凭据存储实例 |
| `identity_code` | 当前身份码（仅内存） |
| `session_id` | 当前场次 ID |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `validate_credentials(app_id, key_id, key_secret)` | 验证开放平台凭证是否有效 |
| `start_session(identity_code)` | 使用身份码启动直播场次 |
| `end_session()` | 结束当前场次 |
| `store_credentials(app_id, key_id, key_secret)` | 安全存储凭证 |
| `load_credentials()` | 从安全存储加载凭证 |
| `has_valid_credentials()` | 检查是否有有效凭证 |
| `clear_identity_code()` | 清除内存中的身份码 |

---

### 4.18 `src/adapter/normalizer.py` — `MessageNormalizer` 类

#### 类：`MessageNormalizer`（消息标准化器）

**职责**：将 Bilibili 原始事件转换为内部统一消息模型

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `event_handlers` | 事件类型→处理函数的映射字典 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `normalize(raw_event)` | 入口：根据原始事件类型分发到对应处理函数 |
| `normalize_danmaku(raw)` | 标准化普通弹幕 → UnifiedMessage |
| `normalize_emotion(raw)` | 标准化表情弹幕 → UnifiedMessage |
| `normalize_gift(raw)` | 标准化付费礼物 → UnifiedMessage |
| `normalize_guard(raw)` | 标准化上舰 → UnifiedMessage |
| `normalize_super_chat(raw)` | 标准化醒目留言 → UnifiedMessage |
| `normalize_sc_delete(raw)` | 标准化醒目留言删除 → UnifiedMessage |
| `extract_user_identity(raw)` | 提取用户身份（粉丝牌/舰队/房管/主播） |
| `extract_fans_medal(raw)` | 提取粉丝牌信息 |
| `build_base_message(raw, msg_type)` | 构建统一的基类消息字段 |

---

### 4.19 `src/adapter/protocol.py` — 协议定义模块

**职责**：定义 Bilibili 开放平台的协议常量和数据结构

**内容**：
- Bilibili WebSocket 命令类型常量
- 事件类型常量
- 错误码常量
- 协议版本
- 请求/响应的数据类定义

---

### 4.20 `src/server/http_server.py` — `HttpServer` 类

#### 类：`HttpServer`（本地 HTTP 服务）

**职责**：提供 OBS 页面和 WebSocket 通信端点的本地 HTTP 服务

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `host` | 监听地址（固定 127.0.0.1） |
| `port` | 监听端口（固定默认端口） |
| `app` | Web 框架应用实例 |
| `ws_handler` | WebSocket 连接管理器 |
| `is_running` | 服务运行状态 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `start()` | 启动 HTTP 服务，端口冲突时停止并提示 |
| `stop()` | 停止 HTTP 服务 |
| `get_status()` | 获取服务状态 |
| `set_port(port)` | 修改端口并提示用户同步更新 OBS URL |
| `register_routes()` | 注册所有 API 路由和 WebSocket 端点 |

---

### 4.21 `src/server/ws_handler.py` — `ObsWsHandler` 类

#### 类：`ObsWsHandler`（OBS WebSocket 连接管理器）

**职责**：管理所有 OBS 来源的 WebSocket 连接、快照分发和增量消息推送

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `connections` | 活跃连接集合 |
| `connection_count` | 当前连接数 |
| `protocol_version` | 协议版本号 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `on_connect(ws)` | OBS 来源连接：发送当前快照（静态，无动画） |
| `on_disconnect(ws)` | OBS 来源断开：不影响其他来源和主播窗口 |
| `send_snapshot(ws)` | 发送当前快照给新连接的 OBS 来源 |
| `send_message(ws, msg)` | 发送增量消息（含更新/删除） |
| `send_style_update(style_config)` | 样式变更后实时同步到所有 OBS 连接 |
| `send_clear()` | 发送清屏命令 |
| `send_clear_with_sc()` | 发送清屏命令（含醒目留言） |
| `broadcast(event)` | 向所有 OBS 连接广播事件 |
| `get_connection_count()` | 获取当前连接数 |

---

### 4.22 `src/server/routes.py` — API 路由

**职责**：定义 HTTP API 路由和 WebSocket 端点

**路由**：
| 路径 | 方法 | 描述 |
|------|------|------|
| `/obs` | WebSocket | OBS 页面 WebSocket 连接端点 |
| `/obs/page` | GET | OBS 透明页面 |
| `/api/status` | GET | 本地服务状态 |
| `/api/health` | GET | 健康检查 |
| `/api/style` | GET/PUT | OBS 样式配置 |
| `/api/filter` | GET/PUT | OBS 过滤规则 |
| `/api/protocol-version` | GET | 协议版本 |

---

### 4.23 `src/config/config_manager.py` — `ConfigManager` 类

#### 类：`ConfigManager`（配置管理器）

**职责**：管理应用配置的读写、校验、备份和版本迁移

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `config_path` | 配置文件路径 |
| `backup_path` | 备份文件路径 |
| `config` | 当前配置数据 |
| `config_version` | 配置版本号 |
| `defaults` | 默认配置值 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `load()` | 加载配置：尝试读取→校验→损坏时恢复备份→失败进入安全默认 |
| `save()` | 保存配置：临时文件+原子替换，保留上次有效备份 |
| `get(key, default)` | 读取配置项 |
| `set(key, value)` | 写入配置项并自动保存 |
| `validate(config)` | 校验配置结构和值的合法性 |
| `migrate(config, from_version)` | 配置版本迁移 |
| `restore_backup()` | 从备份恢复配置 |
| `export_safe()` | 导出脱敏配置（供诊断包） |

---

### 4.24 `src/config/schema.py` — 配置结构定义

**职责**：定义配置的完整数据结构和默认值

**内容**：
- `AppConfig` — 应用配置（版本、端口、关闭行为、启动行为）
- `WindowConfig` — 窗口配置（位置、尺寸、置顶、透明度）
- `ObsStyleConfig` — OBS 样式（主题、字体、字号、颜色、描边、头像/徽章显隐）
- `ObsFilterConfig` — OBS 过滤（用户列表、关键词列表、礼物金额阈值）
- `CredentialsConfig` — 凭证配置（App ID、Access Key ID，Secret 在安全存储中）

---

### 4.25 `src/config/secret_store.py` — `SecretStore` 类

#### 类：`SecretStore`（安全凭据存储）

**职责**：使用 Windows 安全凭据存储 API 保护 Secret 等敏感信息

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `service_name` | Windows 凭据管理器中的服务名称 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `store(key, value)` | 安全存储凭据 |
| `retrieve(key)` | 读取凭据（界面默认遮挡） |
| `delete(key)` | 删除凭据 |
| `is_available()` | 检查 Windows 凭据存储是否可用 |

---

### 4.26 `src/diagnostics/logger.py` — 日志配置

**职责**：配置日志系统

**函数**：
| 函数名 | 描述 |
|--------|------|
| `setup_logging(log_dir)` | 配置日志：单文件 10MB、保留 5 个轮转文件、配置脱敏过滤器 |
| `SanitizingFormatter` | 脱敏格式化器类：移除日志中的身份码/Secret/Cookie/完整用户ID |

---

### 4.27 `src/diagnostics/diagnostic_pack.py` — `DiagnosticPack` 类

#### 类：`DiagnosticPack`（脱敏诊断包生成器）

**职责**：生成脱敏诊断包 ZIP 文件

**属性**：
| 属性名 | 中文描述 |
|--------|----------|
| `pack_path` | 诊断包输出路径 |

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `generate()` | 生成诊断包：包含版本、Windows信息、三层状态、端口、OBS连接数、脱敏配置、近期日志 |
| `_collect_system_info()` | 收集系统信息 |
| `_collect_connection_state()` | 收集连接状态 |
| `_collect_safe_config()` | 收集脱敏后的配置（不含弹幕正文/用户ID/身份码/Secret/Cookie） |
| `_collect_logs()` | 收集近期日志文件 |
| `_sanitize()` | 脱敏处理函数 |

---

### 4.28 `src/diagnostics/mock_events.py` — `MockEventGenerator` 类

#### 类：`MockEventGenerator`（模拟消息生成器）

**职责**：生成模拟的四类核心消息，用于测试和开发，不发送到 Bilibili

**方法**：
| 方法名 | 中文描述 |
|--------|----------|
| `generate_danmaku()` | 生成模拟普通弹幕 |
| `generate_gift()` | 生成模拟付费礼物 |
| `generate_guard()` | 生成模拟上舰 |
| `generate_super_chat()` | 生成模拟醒目留言 |
| `generate_sc_delete()` | 生成模拟醒目留言删除 |
| `generate_burst(count)` | 生成指定数量的模拟弹幕（用于压力测试） |

---

### 4.29 `src/utils/singleton.py` — 单实例管理

**函数**：
| 函数名 | 描述 |
|--------|------|
| `check_single_instance(port)` | 使用文件锁或命名管道确保只运行一个主实例 |
| `notify_existing_instance(port)` | 向已有实例发送唤醒信号 |

---

### 4.30 `src/utils/text_filter.py` — 关键词匹配

**函数**：
| 函数名 | 描述 |
|--------|------|
| `parse_keywords(text)` | 解析关键词输入（一行一个、去空格、去重、忽略空行） |
| `match_keywords(text, keywords)` | 判断文本是否包含任一关键词（不区分大小写） |

---

## 5. 验证方式

1. **单元测试**：对 `normalizer`、`obs_filter`、`gift_merger`、`pinned_manager`、`message_buffer`、`snapshot`、`config` 模块编写 `test_*.py`
2. **协议测试**：验证 WebSocket 快照/增量/更新/删除/清屏事件的正确性
3. **集成测试**：使用 Mock Bilibili 消息源验证完整消息链路
4. **压力测试**：验证 20 条/秒持续、100 条/秒峰值下的稳定性
5. **长时间运行**：8 小时稳定性测试，检查内存是否持续增长
6. **CI**：每次 push 运行单元测试+协议测试，Python 3.12，Ubuntu runner
