"""v0.8 Phase 6: 把 memory index 灌进 system prompt 的端到端测试。"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.loop import Agent, _read_memory_indices
from baozicode.config.schema import (
    AgentConfig,
    AppConfig,
    BackendConfig,
    MemoryConfig,
)
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import LLMClient
from baozicode.memory import IndexEntry, MemoryStore, Note, NoteType
from baozicode.prompt.builder import PromptBuilder
from baozicode.prompt.rules import RuleRegistry


class _NullLLM(LLMClient):
    async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):
        if False:
            yield  # pragma: no cover


def _make_config(user_dir: Path, project_dir: Path) -> AppConfig:
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
        memory=MemoryConfig(user_dir=user_dir, project_dir=project_dir),
    )


# ---- sections/memory.py 单独测试 ----


def test_section_empty_returns_empty() -> None:
    """两层都为空 → 返回 ""。"""
    from baozicode.prompt.sections import memory as mem_section
    from baozicode.prompt.types import BuildContext

    class _Cfg:
        memory_path = None

    class _Rules:
        pass

    ctx = BuildContext(config=_Cfg(), rule_registry=_Rules())  # type: ignore[arg-type]
    out = mem_section.render(ctx)
    assert out == ""


def test_section_only_user() -> None:
    """仅 user 层非空 → 渲染单层, 不渲染 project。"""
    from baozicode.prompt.sections import memory as mem_section
    from baozicode.prompt.types import BuildContext

    class _Cfg:
        memory_path = None

    class _Rules:
        pass

    ctx = BuildContext(
        config=_Cfg(), rule_registry=_Rules(),  # type: ignore[arg-type]
        memory_index_user="## [user-pref] uses-chinese — 中文\nuser 偏好中文",
    )
    out = mem_section.render(ctx)
    assert "## 长期记忆 (用户级)" in out
    assert "uses-chinese" in out
    assert "## 长期记忆 (项目级)" not in out
    print("[OK] 仅 user 层 → 渲染单层")


def test_section_only_project() -> None:
    """仅 project 层非空 → 渲染单层。"""
    from baozicode.prompt.sections import memory as mem_section
    from baozicode.prompt.types import BuildContext

    class _Cfg:
        memory_path = None

    class _Rules:
        pass

    ctx = BuildContext(
        config=_Cfg(), rule_registry=_Rules(),  # type: ignore[arg-type]
        memory_index_project="## [project] uses-pydantic — Pydantic v2\nproject 依赖",
    )
    out = mem_section.render(ctx)
    assert "## 长期记忆 (项目级)" in out
    assert "uses-pydantic" in out
    assert "## 长期记忆 (用户级)" not in out
    print("[OK] 仅 project 层 → 渲染单层")


def test_section_both_layers() -> None:
    """两层都非空 → 渲染两层, 用户级在前, 项目级在后。"""
    from baozicode.prompt.sections import memory as mem_section
    from baozicode.prompt.types import BuildContext

    class _Cfg:
        memory_path = None

    class _Rules:
        pass

    ctx = BuildContext(
        config=_Cfg(), rule_registry=_Rules(),  # type: ignore[arg-type]
        memory_index_user="## [user-pref] u — user",
        memory_index_project="## [project] p — project",
    )
    out = mem_section.render(ctx)
    user_pos = out.find("## 长期记忆 (用户级)")
    project_pos = out.find("## 长期记忆 (项目级)")
    assert user_pos >= 0
    assert project_pos > user_pos, "user 应在 project 之前"
    assert "\n\n" in out  # 两层用 \n\n 分隔
    print("[OK] 双层都非空 → user 在前, project 在后")


def test_section_deprecated_memory_path_fallback(tmp_path: Path) -> None:
    """config.memory_path 存在 + 两层都空 → 退回旧单文件。"""
    from baozicode.prompt.sections import memory as mem_section
    from baozicode.prompt.types import BuildContext

    # 写一个旧 memory.md
    legacy = tmp_path / "old_memory.md"
    legacy.write_text("# 旧记忆\nuser likes 中文\n", encoding="utf-8")

    class _Cfg:
        memory_path = legacy

    class _Rules:
        pass

    ctx = BuildContext(config=_Cfg(), rule_registry=_Rules())  # type: ignore[arg-type]
    out = mem_section.render(ctx)
    assert "## 长期记忆" in out
    assert "旧记忆" in out
    print("[OK] 旧 memory_path 存在 → fallback 到单文件")


def test_section_deprecated_fallback_skipped_when_new_dirs_have_data(tmp_path: Path) -> None:
    """新两层都有数据 → 不用旧 memory_path fallback。"""
    from baozicode.prompt.sections import memory as mem_section
    from baozicode.prompt.types import BuildContext

    legacy = tmp_path / "old_memory.md"
    legacy.write_text("# 旧记忆 — 应不出现\n", encoding="utf-8")

    class _Cfg:
        memory_path = legacy

    class _Rules:
        pass

    ctx = BuildContext(
        config=_Cfg(), rule_registry=_Rules(),  # type: ignore[arg-type]
        memory_index_user="## [user-pref] new — 新笔记",
        memory_index_project="## [project] np — new project",
    )
    out = mem_section.render(ctx)
    assert "## 长期记忆 (用户级)" in out
    assert "## 长期记忆 (项目级)" in out
    assert "旧记忆 — 应不出现" not in out
    print("[OK] 新两层有数据 → 不退化到旧 fallback")


# ---- PromptBuilder 集成 ----


def test_prompt_builder_includes_memory_index(tmp_path: Path) -> None:
    """PromptBuilder.build() 接收 memory_index_* 后应注入到 stable_system。"""
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    MemoryStore(user_dir, scope="user")  # ensure root
    MemoryStore(project_dir, scope="project")

    cfg = _make_config(user_dir, project_dir)
    bp = PromptBuilder(rule_registry=RuleRegistry()).build(
        cfg, plan_mode=False, tools=[],
        memory_index_user="## [user-pref] u — user",
        memory_index_project="## [project] p — project",
    )
    assert "## 长期记忆 (用户级)" in bp.stable_system
    assert "## 长期记忆 (项目级)" in bp.stable_system
    print("[OK] PromptBuilder 注入两层 index 到 stable_system")


def test_prompt_builder_empty_indices_skip_section(tmp_path: Path) -> None:
    """两个 index 都空 → 内存 section 不出现。"""
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    MemoryStore(user_dir, scope="user")
    MemoryStore(project_dir, scope="project")

    cfg = _make_config(user_dir, project_dir)
    bp = PromptBuilder(rule_registry=RuleRegistry()).build(
        cfg, plan_mode=False, tools=[],
    )
    assert "## 长期记忆" not in bp.stable_system
    print("[OK] 两层空 → 不渲染 memory section")


# ---- _read_memory_indices helper ----


def test_read_memory_indices_empty_returns_none(tmp_path: Path) -> None:
    """空 store → (None, None)。"""
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    cfg = _make_config(user_dir, project_dir)
    u, p = _read_memory_indices(cfg, project_root=tmp_path)
    assert u is None
    assert p is None
    print("[OK] 空 store → (None, None)")


def test_read_memory_indices_with_entries(tmp_path: Path) -> None:
    """有 notes → 返回对应 index 文本。"""
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_store = MemoryStore(user_dir, scope="user")
    project_store = MemoryStore(project_dir, scope="project")

    # user 加 1 条
    user_store.add_note(Note(
        type=NoteType.USER_PREF, slug="u-pref", title="中文",
        content="用户偏好中文", created_at=datetime.now(timezone.utc),
        source_session="s1",
    ))
    user_store.rewrite_index([
        IndexEntry(slug="u-pref", type=NoteType.USER_PREF,
                   title="中文", one_liner="user 偏好中文")
    ], max_lines=200, max_bytes=25_600)

    # project 加 1 条
    project_store.add_note(Note(
        type=NoteType.PROJECT, slug="p-fact", title="Pydantic v2",
        content="项目用 Pydantic v2", created_at=datetime.now(timezone.utc),
        source_session="s1",
    ))
    project_store.rewrite_index([
        IndexEntry(slug="p-fact", type=NoteType.PROJECT,
                   title="Pydantic v2", one_liner="项目依赖 Pydantic v2")
    ], max_lines=200, max_bytes=25_600)

    cfg = _make_config(user_dir, project_dir)
    u, p = _read_memory_indices(cfg, project_root=tmp_path)
    assert u is not None
    assert "中文" in u
    assert p is not None
    assert "Pydantic" in p
    print("[OK] 有 notes → 返回 (user_text, project_text)")


# ---- Agent 端到端:memory 注入 system prompt ----


def test_agent_system_prompt_contains_memory_index(tmp_path: Path) -> None:
    """Agent 构造时,两层 memory index 应被读出并灌进 self._prompt。"""
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_store = MemoryStore(user_dir, scope="user")
    project_store = MemoryStore(project_dir, scope="project")

    user_store.rewrite_index([
        IndexEntry(slug="u", type=NoteType.USER_PREF, title="U", one_liner="u-line")
    ], max_lines=200, max_bytes=25_600)
    project_store.rewrite_index([
        IndexEntry(slug="p", type=NoteType.PROJECT, title="P", one_liner="p-line")
    ], max_lines=200, max_bytes=25_600)

    cfg = _make_config(user_dir, project_dir)
    a = Agent(
        llm_client=_NullLLM(),
        tools=[],
        conversation=ConversationManager(),
        permissions=type("P", (), {"deny": [], "auto_allow": []})(),
        config=cfg,
        project_root=tmp_path,
    )
    sys_prompt = a.prompt.stable_system
    assert "## 长期记忆 (用户级)" in sys_prompt
    assert "## 长期记忆 (项目级)" in sys_prompt
    assert "u-line" in sys_prompt
    assert "p-line" in sys_prompt
    print("[OK] Agent 构造时自动灌入 memory index")
