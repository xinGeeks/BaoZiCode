"""v0.9 slash 分流 — 决定用户输入走本地命令 vs 走 Agent。

`dispatch(input, ctx, registry, on_agent)` 是 sync 接口,内部按事件分两条路:

1. 空 / 空白输入 → no-op
2. 首字非 `/` → 调 `on_agent(text)`(异步或同步由 caller 决定)
3. 首字是 `/` → parse → registry.lookup
   - 未命中 → ctx.show_error("未知命令: ...")
   - 命中 → 异步调 handler(args, ctx),根据返回值决定下一步动作:
     * LocalResult / UiStateResult → 已由 handler 处理,无 chat 回显
     * PromptResult(text) → 调 on_agent(text)

`parse_command(input)` 是纯函数,只切第一空格,导出供测试用。
"""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Union

from baozicode.commands.registry import (
    CommandResult,
    LocalResult,
    PromptResult,
    UiStateResult,
)

if TYPE_CHECKING:
    from baozicode.commands.context import CommandContext
    from baozicode.commands.registry import CommandDef, CommandRegistry


@dataclass(frozen=True)
class ParsedCommand:
    """parse_command 的返回类型。"""

    name: str         # lowercase canonical key
    args: str         # 第一个空格之后(stripped)


_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")


def parse_command(input_text: str) -> ParsedCommand | None:
    """把 slash 输入切成 (name, args)。

    Rules:
    - 输入前导 `/` 可选保留,内部统一去掉
    - 切第一空格(无空格 → args="")
    - args 自动 strip 首尾空白
    - 不是 slash 输入 → return None
    - 输入空白 / 空 → return None
    """
    raw = input_text.strip()
    if not raw:
        return None
    if not raw.startswith("/"):
        return None
    body = raw[1:].strip()
    if not body:
        return None
    name, _, args = body.partition(" ")
    name = name.lower()
    if not _NAME_RE.match(name):
        return None
    return ParsedCommand(name=name, args=args.strip())


async def dispatch(
    input_text: str,
    ctx: "CommandContext",
    registry: "CommandRegistry",
    on_agent: Callable[[str], Union[Awaitable[None], None]],
) -> None:
    """slash 分流主入口。

    Args:
        input_text: 用户在 input box 的原始字符串
        ctx: CommandContext(由 caller 构造)
        registry: 已 freeze 的 CommandRegistry
        on_agent: 普通消息的处理函数,由 caller 提供(通常 wrapper 调 Agent.run + 显示)
    """
    raw = input_text.strip()
    if not raw:
        return
    # 非 slash → 直接给 Agent
    parsed = parse_command(raw)
    if parsed is None and not raw.startswith("/"):
        await _maybe_await(on_agent(raw))
        return
    # slash 但 name 不合法
    if parsed is None:
        ctx.show_error(f"未知命令: {raw}. 输入 /help 查看可用命令")
        return
    def_ = registry.lookup(parsed.name)
    if def_ is None:
        ctx.show_error(
            f"未知命令: /{parsed.name}. 输入 /help 查看可用命令"
        )
        return
    # 命中 → 跑 handler
    try:
        result = await _invoke_handler(def_.handler, parsed.args, ctx)
    except Exception as exc:  # noqa: BLE001
        ctx.show_error(f"[{parsed.name}] 命令出错: {type(exc).__name__}: {exc}")
        return
    # 根据 CommandResult 类型决定下一步
    if isinstance(result, PromptResult):
        await _maybe_await(on_agent(result.text))


async def _invoke_handler(handler, args: str, ctx: "CommandContext") -> CommandResult:
    """handler 必须是 async coroutine。sync handler 也接受(Python 会跑阻塞)。"""
    if handler is None:
        # 不应发生(registry.register 不允许 None handler),但 defensive
        raise RuntimeError("command handler 未注册")
    out = handler(args, ctx)
    if inspect.iscoroutine(out):
        return await out
    return out  # type: ignore[return-value]


async def _maybe_await(callable_or_coro):
    """若返回值是 awaitable,await 它;否则当 None/noreturn 类型返回立即走。"""
    if callable_or_coro is None:
        return
    if inspect.iscoroutine(callable_or_coro):
        await callable_or_coro
    return


__all__ = ["ParsedCommand", "parse_command", "dispatch"]
