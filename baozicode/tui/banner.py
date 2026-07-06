"""ASCII 包子 banner。"""

from __future__ import annotations

BAOZI_BANNER = r"""
        .-^^---.
       /  o   o \
      |    ^     |     BaoZiCode
       \   ___  /      v0.1 · TUI 多轮 AI 编码助手
        '-._____.-'
       /___________\
      |  蒸  笼  笼  |
      |_____________|
"""

WELCOME_TEMPLATE = """\
欢迎使用 **BaoZiCode** 🥟

- 后端：`{backend}` · 模型：`{model}`
- 输入消息后回车发送；输入 `/help` 查看可用命令。
- 流式输出期间输入框会自动锁定，输出完成后自动解锁。
"""
