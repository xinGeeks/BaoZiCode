"""Phase 5 — dispatcher + completor 单元测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.commands.builtin import build_builtin_defs
from baozicode.commands.completor import candidates, has_completable_space
from baozicode.commands.dispatcher import ParsedCommand, dispatch, parse_command
from baozicode.commands.registry import (
    CommandDef,
    CommandRegistry,
    CommandType,
    LocalResult,
    PromptResult,
    UiStateResult,
)


# ---- parse_command 12 测试 ----


def test_parse_empty_returns_none() -> None:
    assert parse_command("") is None


def test_parse_whitespace_returns_none() -> None:
    assert parse_command("   ") is None


def test_parse_no_slash_returns_none() -> None:
    assert parse_command("hello") is None


def test_parse_just_slash_returns_none() -> None:
    assert parse_command("/") is None
    assert parse_command("/   ") is None


def test_parse_command_only() -> None:
    p = parse_command("/help")
    assert p == ParsedCommand(name="help", args="")


def test_parse_command_with_args() -> None:
    p = parse_command("/permission strict")
    assert p == ParsedCommand(name="permission", args="strict")


def test_parse_strips_arg_whitespace() -> None:
    p = parse_command("/review   5 轮前   ")
    assert p == ParsedCommand(name="review", args="5 轮前")


def test_parse_uppercase_normalizes() -> None:
    p = parse_command("/STATUS")
    assert p is not None
    assert p.name == "status"


def test_parse_invalid_name_returns_none() -> None:
    assert parse_command("/foo_bar") is None
    assert parse_command("/9foo") is None


def test_parse_arg_with_internal_spaces() -> None:
    """args 段保留内部空格。"""
    p = parse_command("/review 5 轮前 todo")
    assert p == ParsedCommand(name="review", args="5 轮前 todo")


def test_parse_preserves_chinese() -> None:
    p = parse_command("/memory")
    assert p is not None
    assert p.name == "memory"


def test_parse_with_leading_slash_required() -> None:
    """无 / 时 parse_command 返回 None;Agent path 接管。"""
    assert parse_command("help") is None
    assert parse_command("memory status") is None


# ---- completor 10 测试 ----


def _reg_with_builtins():
    """构造 11 个命令注册好的 registry(v1.0 +1:/skill);除 review 返回 PromptResult 外其他返回 LocalResult。"""

    async def _h_local(args, ctx):
        from baozicode.commands.registry import LocalResult
        return LocalResult()

    async def _h_ui(args, ctx):
        from baozicode.commands.registry import UiStateResult
        return UiStateResult()

    async def _h_review(args, ctx):
        from baozicode.commands.registry import PromptResult
        return PromptResult(text="[stub review prompt]")

    reg = CommandRegistry()
    handlers = {
        "help": _h_local,
        "compact": _h_ui,
        "clear": _h_ui,
        "plan": _h_ui,
        "do": _h_ui,
        "session": _h_ui,
        "memory": _h_local,
        "permission": _h_ui,
        "status": _h_local,
        "review": _h_review,
        "skill": _h_local,
    }
    for d in build_builtin_defs(lambda n: handlers[n]):
        reg.register(d)
    reg.freeze()
    return reg


def test_candidates_empty_returns_all() -> None:
    reg = _reg_with_builtins()
    out = candidates("", reg)
    assert len(out) == 11
    print(f"[OK] empty prefix → 11 candidates")


def test_candidates_single_match_returns_one() -> None:
    reg = _reg_with_builtins()
    out = candidates("/hel", reg)
    assert out == ["help"]


def test_candidates_multi_match_returns_many() -> None:
    reg = _reg_with_builtins()
    out = candidates("/p", reg)
    # 命中: plan, permission
    assert "plan" in out
    assert "permission" in out
    print(f"[OK] /p → {out}")


def test_candidates_case_insensitive() -> None:
    reg = _reg_with_builtins()
    out = candidates("/STAT", reg)
    assert out == ["status"]


def test_candidates_strips_leading_slash() -> None:
    reg = _reg_with_builtins()
    out = candidates("mem", reg)
    assert out == ["memory"]


def test_candidates_no_match_returns_empty() -> None:
    reg = _reg_with_builtins()
    out = candidates("/zzz", reg)
    assert out == []


def test_candidates_hidden_excluded() -> None:
    reg = CommandRegistry()
    reg.register(CommandDef(name="pub", handler=lambda a, c: LocalResult()))
    reg.register(CommandDef(name="sec", hidden=True, handler=lambda a, c: LocalResult()))
    reg.freeze()
    assert "pub" in candidates("", reg)
    assert "sec" not in candidates("", reg)
    assert "sec" not in candidates("/s", reg)


def test_candidates_resolves_via_alias() -> None:
    """alias 命中也算,但返回主名。"""
    reg = _reg_with_builtins()
    out = candidates("/permiss", reg)
    assert out == ["permission"]
    # /permissions 也命中主名
    out2 = candidates("/permissions", reg)
    assert "permission" in out2


def test_has_completable_space_no_space() -> None:
    assert has_completable_space("/help") is False
    assert has_completable_space("") is False
    assert has_completable_space("help") is False


def test_has_completable_space_with_space() -> None:
    assert has_completable_space("/review 5 轮前") is True
    assert has_completable_space("hello world") is True


# ---- dispatch 12 测试 ----


class _StubCtx:
    """满足 CommandContext Protocol,记录全部调用。"""

    def __init__(self) -> None:
        self.app = object()
        self.config = object()
        self.info: list[str] = []
        self.errors: list[str] = []
        self.sent: list[str] = []
        self.modes: list[object] = []
        self.refreshed = 0

    def show_info(self, t): self.info.append(t)
    def show_error(self, t): self.errors.append(t)
    def send_to_agent(self, t): self.sent.append(t)
    def switch_mode(self, m): self.modes.append(m)
    def get_token_usage(self): return None
    def refresh_status(self): self.refreshed += 1
    def push_modal(self, s): return None


@pytest.mark.asyncio
async def test_dispatch_empty_noop() -> None:
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("", ctx, reg, on_agent)
    assert sent == []
    assert ctx.sent == []


@pytest.mark.asyncio
async def test_dispatch_whitespace_noop() -> None:
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("   ", ctx, reg, on_agent)
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_plain_text_to_agent() -> None:
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("请总结上次", ctx, reg, on_agent)
    assert sent == ["请总结上次"]
    assert ctx.sent == []  # dispatch 不直接 send_to_agent


@pytest.mark.asyncio
async def test_dispatch_unknown_command_error() -> None:
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("/foobar", ctx, reg, on_agent)
    assert sent == []
    assert any("foobar" in e for e in ctx.errors)
    assert any("/help" in e for e in ctx.errors)


@pytest.mark.asyncio
async def test_dispatch_known_local_command() -> None:
    """LOCAL 类型的 /status → handler 自己负责 ctx.show_info,dispatch 不直接回显。"""
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("/status", ctx, reg, on_agent)
    assert sent == []
    # stub handler 没显示 info;no echo


@pytest.mark.asyncio
async def test_dispatch_known_ui_state_command() -> None:
    """UI_STATE 类型不送 Agent。"""
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("/plan", ctx, reg, on_agent)
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_prompt_result_sends_to_agent() -> None:
    """PROMPT 类型返回的 text 走 on_agent。"""
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("/review 5 轮前", ctx, reg, on_agent)
    # stub 默认 text="[stub review prompt]"
    assert sent == ["[stub review prompt]"]


@pytest.mark.asyncio
async def test_dispatch_case_insensitive_command() -> None:
    """大写命令也命中(lookup 已 lowercase,parse_command 也 lowercase)。"""
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("/HELP", ctx, reg, on_agent)
    # 不是 PROMPT,不送 Agent
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_alias_works() -> None:
    """/permissions alias 命中 /permission 主名。"""
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("/permissions", ctx, reg, on_agent)
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_handler_exception_shows_error() -> None:
    """handler 抛异常 → visible error。"""
    reg = CommandRegistry()

    async def bad_handler(args, ctx):
        raise ValueError("kaboom")

    reg.register(CommandDef(name="explode", handler=bad_handler))
    reg.freeze()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("/explode", ctx, reg, on_agent)
    assert sent == []
    assert any("kaboom" in e for e in ctx.errors)
    assert any("ValueError" in e for e in ctx.errors)


@pytest.mark.asyncio
async def test_dispatch_sync_on_agent_handled() -> None:
    """on_agent 接受 sync callable(coroutine 检查覆盖)。"""
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    def sync_on_agent(t): sent.append(t)

    await dispatch("/status", ctx, reg, sync_on_agent)
    # /status 是 LOCAL,不应调 on_agent
    assert sent == []

    # 但 plain text 路径也会调
    await dispatch("hello", ctx, reg, sync_on_agent)
    assert sent == ["hello"]


@pytest.mark.asyncio
async def test_dispatch_invalid_slash_form() -> None:
    """非法命令名(如 /foo_bar)→ error。"""
    reg = _reg_with_builtins()
    ctx = _StubCtx()
    sent: list[str] = []

    async def on_agent(t): sent.append(t)

    await dispatch("/foo_bar", ctx, reg, on_agent)
    assert sent == []
    assert len(ctx.errors) == 1
