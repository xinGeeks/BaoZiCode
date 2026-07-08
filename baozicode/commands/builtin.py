"""v0.9 内置 slash 命令元数据。

handler 不在此模块 — 由 `tui/chat_screen.py` 提供实现,通过
`build_builtin_defs(handler_provider)` 拿到具体引用并组装成 11 个
`CommandDef`,然后注册到 registry。

设计原因:
- builtin.py 不依赖 textual(保持 commands/ 的纯净)
- handler 必须访问 chat_screen 的内部状态(plan_mode / session_id / 等),
  这些都在 tui/chat_screen.py 私有
- 通过 handler 字典 (Dict[str, Callable]) 注入,使注册动作与具体
  handler 解耦
"""

from __future__ import annotations

from typing import Awaitable, Callable

from baozicode.commands.registry import CommandDef, CommandType

# handler 提供者签名:provider(name: str) -> async (args, ctx) -> CommandResult
HandlerProvider = Callable[[str], Callable[..., Awaitable[object]]]


def build_builtin_defs(get_handler: HandlerProvider) -> tuple[CommandDef, ...]:
    """构造 11 个内置 `CommandDef`(v1.0 +1:`/skill`)。

    Args:
        get_handler: 给定命令主名,返回 async handler 函数。
        tui/chat_screen.py 提供这个 getter,把每个名字路由到具体方法。
    """
    return (
        CommandDef(
            name="help",
            description="显示可用命令列表",
            usage="/help",
            type=CommandType.LOCAL,
            handler=get_handler("help"),
        ),
        CommandDef(
            name="compact",
            description="手动压缩上下文(Layer 1 offload + Layer 2 摘要)",
            usage="/compact",
            type=CommandType.UI_STATE,
            handler=get_handler("compact"),
        ),
        CommandDef(
            name="clear",
            description="清空当前对话历史",
            usage="/clear",
            type=CommandType.UI_STATE,
            handler=get_handler("clear"),
        ),
        CommandDef(
            name="plan",
            description="进入 plan mode(只读工具),args 静默忽略",
            usage="/plan",
            type=CommandType.UI_STATE,
            handler=get_handler("plan"),
        ),
        CommandDef(
            name="do",
            description="退出 plan mode,恢复所有工具,args 静默忽略",
            usage="/do",
            type=CommandType.UI_STATE,
            handler=get_handler("do"),
        ),
        CommandDef(
            name="session",
            description="弹窗选择已有 session / 开新 session / 取消",
            usage="/session",
            type=CommandType.UI_STATE,
            handler=get_handler("session"),
        ),
        CommandDef(
            name="memory",
            description="显示 user / project 两层记忆的状态",
            usage="/memory",
            type=CommandType.LOCAL,
            handler=get_handler("memory"),
        ),
        CommandDef(
            name="permission",
            aliases=("permissions",),
            description="查看或切换权限 mode (strict/default/permissive)",
            usage="/permission [default|strict|permissive|auto]",
            type=CommandType.UI_STATE,
            params_hint="[<mode>]",
            handler=get_handler("permission"),
        ),
        CommandDef(
            name="status",
            description="显示 session_id / 后端 / model / token 累计 / memory 摘要",
            usage="/status",
            type=CommandType.LOCAL,
            handler=get_handler("status"),
        ),
        CommandDef(
            name="review",
            description="让 Agent 审查当前会话自 {since} 起的所有改动",
            usage="/review [<since>]",
            type=CommandType.PROMPT,
            params_hint="[<since>]",
            handler=get_handler("review"),
        ),
        CommandDef(
            name="skill",
            description="管理 Skill:`/skill list` 列出可见 Skill;"
            "`/skill <name> [args...]` 加载并激活;"
            "`/skill clear` 清空已激活",
            usage="/skill <list|<name> [args...]|clear>",
            type=CommandType.LOCAL,
            params_hint="<list|<name>|clear>",
            handler=get_handler("skill"),
        ),
    )


__all__ = ["build_builtin_defs", "HandlerProvider"]