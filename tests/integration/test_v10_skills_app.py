"""v1.0 Skills — App 启动时 Skill bootstrap 集成测试。

覆盖:
- App.__init__ 后 `app.skills` 是 SkillSet
- 默认 builtin Skill 都进了 registry
- `_register_load_skill_tool` worker 把 load_skill 加进 ToolRegistry
- `app.skills.loader.execute` 能正确路由到 load_skill
- ToolRegistry 已注册过 load_skill → 重复 register 应捕获(已注册标记)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.app import BaoZiCodeApp
from baozicode.config.schema import (
    AppConfig,
    BackendConfig,
    MemoryConfig,
    SessionConfig,
)
from baozicode.skills.bootstrap import SkillSet
from baozicode.tools.registry import get_default_tool_registry, unregister_mcp_tools


@pytest.fixture
def app(tmp_path: Path) -> BaoZiCodeApp:
    """构造最小可用 App(同 v0.9 集成测试 fixture)。"""
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
        memory=MemoryConfig(
            user_dir=tmp_path / "user_memory",
            project_dir=tmp_path / "project_memory",
        ),
        sessions=SessionConfig(dir=tmp_path / "sessions"),
    )
    return BaoZiCodeApp(config=cfg, project_root=tmp_path)


# ---- App.skills 装配 ----


def test_app_has_skills_set(app: BaoZiCodeApp) -> None:
    """App.__init__ 完成后 app.skills 是 SkillSet 实例。"""
    assert isinstance(app.skills, SkillSet)


def test_app_default_skills_loaded(app: BaoZiCodeApp) -> None:
    """App 默认把包内 3 个 builtin Skill 装进 registry。"""
    names = {sd.name for sd in app.skills.registry.list_all()}
    assert {"commit", "review", "test"}.issubset(names)


def test_app_skill_filter_factory_works(app: BaoZiCodeApp) -> None:
    """app.skills.build_skill_filter(tool_registry) 返回 SkillWhitelistFilter。"""
    from baozicode.skills.whitelist import SkillWhitelistFilter

    filt = app.skills.build_skill_filter(get_default_tool_registry())
    assert isinstance(filt, SkillWhitelistFilter)


# ---- load_skill tool 注册 ----


@pytest.mark.asyncio
async def test_register_load_skill_tool_adds_to_registry(app: BaoZiCodeApp) -> None:
    """调一次 `_register_load_skill_tool` 后,ToolRegistry 含 load_skill。"""
    # 清理副作用(其他测试可能没注册)
    reg = get_default_tool_registry()
    if "load_skill" in reg.mcp_tool_names():
        await reg.unregister_mcp_tools(["load_skill"])

    await app._register_load_skill_tool()

    assert "load_skill" in reg.get_tool_names()
    assert app._load_skill_tool_registered is True


@pytest.mark.asyncio
async def test_register_load_skill_tool_idempotent(app: BaoZiCodeApp) -> None:
    """重复注册(已注册过)不会 panic — 标记为已注册即可。"""
    reg = get_default_tool_registry()
    if "load_skill" in reg.mcp_tool_names():
        await reg.unregister_mcp_tools(["load_skill"])

    await app._register_load_skill_tool()
    # 再调一次(已注册)→ 不抛
    await app._register_load_skill_tool()
    assert "load_skill" in reg.get_tool_names()


@pytest.mark.asyncio
async def test_load_skill_tool_routes_to_loader(app: BaoZiCodeApp) -> None:
    """ToolRegistry.execute(load_skill) 委派到 SkillLoader.execute。"""
    from baozicode.tools.base import ToolCall
    from baozicode.tools.registry import get_default_tool_registry

    reg = get_default_tool_registry()
    if "load_skill" in reg.mcp_tool_names():
        await reg.unregister_mcp_tools(["load_skill"])

    await app._register_load_skill_tool()

    call = ToolCall(id="t1", name="load_skill", arguments={"name": "commit"})
    result = await reg.execute_tool_call(call)
    assert not result.is_error
    assert "commit" in result.content


# ---- /clear 清掉 active Skills(走 app.skills.activation.clear)----


@pytest.mark.asyncio
async def test_clear_conversation_clears_active_skills(app: BaoZiCodeApp) -> None:
    """/clear 视作新会话:已激活 Skill 也清空。"""
    app.skills.loader.load_skill("commit")
    assert app.skills.activation.is_active("commit")

    # 直接调 activation.clear()(等同于 /clear 流程里的那段)
    app.skills.activation.clear()
    assert not app.skills.activation.is_active("commit")