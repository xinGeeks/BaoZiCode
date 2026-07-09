"""v1.2 SubAgent Delegation — 单元测试。

覆盖:
- agent-registry spec 8 个核心 case
- agent-runtime spec 10 个核心 case(后续 runtime 实现后追加)
- subagent-manager spec 12 个核心 case(后续 manager 实现后追加)
- skill-execution DELTA 3 个 case
- hooks-lifecycle DELTA 2 个 case
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from baozicode.agents import (
    AgentDef,
    AgentFrontmatter,
    AgentRegistry,
    MaxConcurrentReachedError,
    MissingPlaceholderError,
    ScanError,
    SubAgentManager,
    SubAgentRuntime,
    TASK_TOOL,
    TaskInfo,
    emit_scan_warnings,
    parse_agent,
    substitute_placeholders,
)
from baozicode.agents.filter import GLOBAL_DENY, ToolFilter, ToolFilterEmptyError
from baozicode.agents.schema import MAX_ITERATIONS, MAX_NESTING_DEPTH
from baozicode.tools.base import ToolDefinition, ToolResult


# ---- helper ----


def write_agent(dir: Path, name: str, content: str) -> Path:
    """在 dir/<name>/AGENT.md 写一个 Agent 文件。"""
    agent_dir = dir / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    p = agent_dir / "AGENT.md"
    p.write_text(content, encoding="utf-8")
    return p


# ---- parse_agent tests ----


class TestParseAgent:
    def test_valid_minimal(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: foo
            description: 测试 agent
            ---
            这是 body。
            """
        )
        fm, body = parse_agent(text)
        assert fm.name == "foo"
        assert fm.description == "测试 agent"
        assert fm.tools is None
        assert fm.tools_deny is None
        assert fm.model is None
        assert fm.max_iterations == 20  # default
        assert fm.permission_mode is None
        assert fm.nesting_depth == 0  # default
        assert fm.hidden is False
        assert "这是 body" in body

    def test_valid_full(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: bar
            description: 完整字段
            tools: [Read, Grep]
            tools-deny: [Bash]
            model: haiku
            max-iterations: 10
            permission-mode: strict
            nesting-depth: 2
            hidden: true
            ---
            # body
            """
        )
        fm, body = parse_agent(text)
        assert fm.name == "bar"
        assert fm.tools == ["Read", "Grep"]
        assert fm.tools_deny == ["Bash"]
        assert fm.model == "haiku"
        assert fm.max_iterations == 10
        assert fm.permission_mode == "strict"
        assert fm.nesting_depth == 2
        assert fm.hidden is True
        assert "# body" in body

    def test_invalid_name_uppercase_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: Foo
            description: test
            ---
            body
            """
        )
        with pytest.raises(ValueError, match="agent name 不合法"):
            parse_agent(text)

    def test_invalid_name_with_space_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: foo bar
            description: test
            ---
            body
            """
        )
        with pytest.raises(ValueError, match="agent name 不合法"):
            parse_agent(text)

    def test_invalid_max_iterations_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: foo
            description: test
            max-iterations: 0
            ---
            """
        )
        with pytest.raises(ValueError, match="max-iterations 越界"):
            parse_agent(text)

    def test_max_iterations_upper_bound(self) -> None:
        text = textwrap.dedent(
            f"""\
            ---
            name: foo
            description: test
            max-iterations: {MAX_ITERATIONS + 1}
            ---
            """
        )
        with pytest.raises(ValueError, match="max-iterations 越界"):
            parse_agent(text)

    def test_invalid_nesting_depth_rejected(self) -> None:
        text = textwrap.dedent(
            f"""\
            ---
            name: foo
            description: test
            nesting-depth: {MAX_NESTING_DEPTH + 1}
            ---
            """
        )
        with pytest.raises(ValueError, match="nesting-depth 越界"):
            parse_agent(text)

    def test_missing_description_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: foo
            ---
            body
            """
        )
        with pytest.raises(ValueError, match="description"):
            parse_agent(text)

    def test_invalid_model_value_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: foo
            description: test
            model: gpt-4-turbo
            ---
            """
        )
        with pytest.raises(ValueError, match="model"):
            parse_agent(text)

    def test_invalid_permission_mode_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: foo
            description: test
            permission-mode: chaos
            ---
            """
        )
        with pytest.raises(ValueError, match="permission-mode"):
            parse_agent(text)

    def test_no_frontmatter_only_body(self) -> None:
        text = "整段当 body,没有 frontmatter"
        # name 和 description 都缺;Pydantic 先报 name(第一个必填)
        with pytest.raises(ValueError, match="name: Field required"):
            parse_agent(text)

    def test_unterminated_frontmatter_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: foo
            description: test
            body 没有第二个 ---
            """
        )
        with pytest.raises(ValueError, match="frontmatter 未正确以"):
            parse_agent(text)

    def test_extra_field_ignored(self) -> None:
        # Pydantic extra="ignore" — 未知字段静默丢弃
        text = textwrap.dedent(
            """\
            ---
            name: foo
            description: test
            unknown_field: value
            ---
            body
            """
        )
        fm, _ = parse_agent(text)
        assert fm.name == "foo"

    def test_tools_deny_duplicate_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: foo
            description: test
            tools-deny: [Bash, Bash]
            ---
            """
        )
        with pytest.raises(ValueError, match="重复"):
            parse_agent(text)


# ---- substitute_placeholders tests ----


class TestSubstitutePlaceholders:
    def test_simple_replacement(self) -> None:
        body = "Hello {name}!"
        assert substitute_placeholders(body, {"name": "World"}) == "Hello World!"

    def test_missing_required_raises(self) -> None:
        with pytest.raises(MissingPlaceholderError):
            substitute_placeholders("Hello {name}!", {})

    def test_default_value_used(self) -> None:
        body = "Hello {name:Guest}!"
        assert substitute_placeholders(body, {}) == "Hello Guest!"
        # args 里有值就用值
        assert substitute_placeholders(body, {"name": "Alice"}) == "Hello Alice!"

    def test_multiple_placeholders(self) -> None:
        body = "{a} + {b} = {a}"
        assert substitute_placeholders(body, {"a": "1", "b": "2"}) == "1 + 2 = 1"

    def test_escape_double_braces(self) -> None:
        body = "{{literal}} and {var}"
        assert (
            substitute_placeholders(body, {"var": "x"})
            == "{literal} and x"
        )

    def test_none_args_treated_as_empty(self) -> None:
        body = "Hello {name:World}!"
        assert substitute_placeholders(body, None) == "Hello World!"


# ---- AgentRegistry.scan tests ----


class TestAgentRegistryScan:
    def test_scan_builtin_only(self, tmp_path: Path) -> None:
        # 模拟 builtin 目录
        builtin = tmp_path / "builtin"
        write_agent(
            builtin, "explorer",
            "---\nname: explorer\ndescription: test\n---\nbody\n",
        )
        reg = AgentRegistry.scan(builtin_dir=builtin)
        assert "explorer" in reg
        assert len(reg) == 1
        assert reg.lookup("explorer").source == "builtin"

    def test_project_overrides_builtin(self, tmp_path: Path) -> None:
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        project = tmp_path / "project"
        write_agent(
            builtin, "explorer",
            "---\nname: explorer\ndescription: builtin-desc\nmodel: haiku\n---\n",
        )
        write_agent(
            user, "explorer",
            "---\nname: explorer\ndescription: user-desc\nmodel: sonnet\n---\n",
        )
        write_agent(
            project, "explorer",
            "---\nname: explorer\ndescription: project-desc\nmodel: opus\n---\n",
        )
        reg = AgentRegistry.scan(
            builtin_dir=builtin, user_dir=user, project_dir=project,
        )
        ad = reg.lookup("explorer")
        assert ad.source == "project"
        assert ad.description == "project-desc"
        assert ad.model == "opus"

    def test_missing_source_dir_skipped(self, tmp_path: Path) -> None:
        # builtin 不存在,user 不存在,只扫 project
        project = tmp_path / "project"
        write_agent(
            project, "foo",
            "---\nname: foo\ndescription: t\n---\n",
        )
        reg = AgentRegistry.scan(project_dir=project)  # builtin / user 都是 None
        assert "foo" in reg
        assert len(reg.scan_errors) == 0

    def test_invalid_file_does_not_block_others(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        # 一个坏文件
        write_agent(
            project, "bad",
            "---\nname: bad\nmax-iterations: 0\n---\n",
        )
        # 一个好文件
        write_agent(
            project, "good",
            "---\nname: good\ndescription: t\n---\n",
        )
        reg = AgentRegistry.scan(project_dir=project)
        assert "good" in reg
        assert "bad" not in reg
        assert len(reg.scan_errors) == 1
        assert "bad" in str(reg.scan_errors[0].path)

    def test_no_agend_md_skipped(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir(parents=True, exist_ok=True)
        # 子目录里没 AGENT.md — 跳过
        (project / "empty_subdir").mkdir(parents=True, exist_ok=True)
        (project / "empty_subdir" / "README.md").write_text("not agent")
        # 孤立的 .md 文件 — 跳过(不在子目录里)
        (project / "orphan.md").write_text("text")
        # 但有 AGENT.md 的要扫到
        write_agent(
            project, "valid",
            "---\nname: valid\ndescription: t\n---\n",
        )
        reg = AgentRegistry.scan(project_dir=project)
        assert len(reg) == 1
        assert "valid" in reg

    def test_valid_tools_check_passes(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        write_agent(
            project, "foo",
            "---\nname: foo\ndescription: t\ntools: [Read, Write]\n---\n",
        )
        reg = AgentRegistry.scan(
            project_dir=project,
            valid_tools={"Read", "Write", "Bash"},
        )
        assert "foo" in reg

    def test_valid_tools_check_fails_systemexit(
        self, tmp_path: Path,
    ) -> None:
        project = tmp_path / "project"
        write_agent(
            project, "foo",
            "---\nname: foo\ndescription: t\ntools: [NonExistentTool]\n---\n",
        )
        with pytest.raises(SystemExit, match="NonExistentTool"):
            AgentRegistry.scan(
                project_dir=project,
                valid_tools={"Read", "Write"},
            )

    def test_plugin_overrides_project(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        write_agent(
            project, "foo",
            "---\nname: foo\ndescription: project-desc\n---\n",
        )
        reg = AgentRegistry.scan(
            project_dir=project,
            plugin_agents=[
                AgentDef(
                    frontmatter=AgentFrontmatter(
                        name="foo", description="plugin-desc",
                    ),
                    body="",
                    source="plugin",
                    path=Path("<mcp://server/foo>"),
                ),
            ],
        )
        ad = reg.lookup("foo")
        assert ad.source == "plugin"
        assert ad.description == "plugin-desc"

    def test_hidden_excluded_from_list_visible(
        self, tmp_path: Path,
    ) -> None:
        project = tmp_path / "project"
        write_agent(
            project, "visible",
            "---\nname: visible\ndescription: t\n---\n",
        )
        write_agent(
            project, "secret",
            "---\nname: secret\ndescription: t\nhidden: true\n---\n",
        )
        reg = AgentRegistry.scan(project_dir=project)
        visible_names = {n for n, _, _ in reg.list_visible()}
        assert "visible" in visible_names
        assert "secret" not in visible_names
        # list_all 仍包含 hidden
        all_names = {ad.name for ad in reg.list_all()}
        assert "secret" in all_names


# ---- AgentRegistry query tests ----


class TestAgentRegistryQuery:
    def test_lookup_not_found(self) -> None:
        reg = AgentRegistry()
        assert reg.lookup("nonexistent") is None

    def test_reload_after_edit(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        write_agent(
            project, "foo",
            "---\nname: foo\ndescription: old\n---\n",
        )
        reg = AgentRegistry.scan(project_dir=project)
        assert reg.lookup("foo").description == "old"

        # 改文件
        (project / "foo" / "AGENT.md").write_text(
            "---\nname: foo\ndescription: new\n---\n",
            encoding="utf-8",
        )
        new_ad = reg.reload("foo")
        assert new_ad.description == "new"

    def test_reload_with_broken_file_retains_old(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        write_agent(
            project, "foo",
            "---\nname: foo\ndescription: original\n---\n",
        )
        reg = AgentRegistry.scan(project_dir=project)

        # 改成坏文件
        (project / "foo" / "AGENT.md").write_text(
            "---\nname: foo\nmax-iterations: 0\n---\n",
            encoding="utf-8",
        )
        # reload 不抛错,保留旧版
        result = reg.reload("foo")
        assert result.description == "original"
        # scan_errors 累积 1 条
        assert len(reg.scan_errors) == 1

    def test_reload_unknown_name_raises(self) -> None:
        reg = AgentRegistry()
        with pytest.raises(KeyError, match="no such agent"):
            reg.reload("nonexistent")

    def test_contains_and_len(self) -> None:
        reg = AgentRegistry()
        assert "foo" not in reg
        assert len(reg) == 0
        reg._defs["foo"] = AgentDef(  # type: ignore[attr-defined]
            frontmatter=AgentFrontmatter(name="foo", description="t"),
            body="",
            source="builtin",
            path=Path("/tmp/foo"),
        )
        assert "foo" in reg
        assert len(reg) == 1


# ---- emit_scan_warnings tests ----


class TestEmitScanWarnings:
    def test_no_errors_no_output(self, capsys) -> None:
        emit_scan_warnings([])
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_single_error(self, capsys) -> None:
        emit_scan_warnings([
            ScanError(path=Path("/tmp/x"), reason="bad"),
        ])
        captured = capsys.readouterr()
        assert "WARN: agent 解析失败" in captured.err
        assert "bad" in captured.err

    def test_multiple_errors_summary(self, capsys) -> None:
        emit_scan_warnings([
            ScanError(path=Path("/tmp/a"), reason="bad"),
            ScanError(path=Path("/tmp/b"), reason="worse"),
        ])
        captured = capsys.readouterr()
        assert "WARN: 2 个 agent 解析失败" in captured.err


# ---- ToolFilter tests ----


def _make_tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name, description=f"test tool {name}", parameters={},
    )


def _make_role(
    name: str = "explorer",
    tools: list[str] | None = None,
    tools_deny: list[str] | None = None,
) -> AgentDef:
    return AgentDef(
        frontmatter=AgentFrontmatter(
            name=name,
            description="test role",
            tools=tools,
            tools_deny=tools_deny,
        ),
        body="role body",
        source="builtin",
        path=Path(f"/tmp/{name}"),
    )


ALL_TOOLS: list[ToolDefinition] = [
    _make_tool("Read"),
    _make_tool("Write"),
    _make_tool("Edit"),
    _make_tool("Bash"),
    _make_tool("Grep"),
    _make_tool("Glob"),
    _make_tool("WebFetch"),
    _make_tool("task"),  # L1 GLOBAL_DENY 必带
]


class TestToolFilter:
    def test_L1_bans_task_globally(self) -> None:
        f = ToolFilter(
            role_def=None, is_background=False,
            background_whitelist=["Read", "Bash", "task"],
            all_tools=ALL_TOOLS,
        )
        names = {t.name for t in f.visible_tools}
        assert "task" not in names
        assert "Read" in names
        assert "Bash" in names

    def test_L2_role_whitelist_narrows(self) -> None:
        role = _make_role(tools=["Read", "Grep"])
        f = ToolFilter(
            role_def=role, is_background=False,
            background_whitelist=[],
            all_tools=ALL_TOOLS,
        )
        names = {t.name for t in f.visible_tools}
        assert names == {"Read", "Grep"}

    def test_L3_role_deny_overrides_L2(self) -> None:
        role = _make_role(tools=["Read", "Write"], tools_deny=["Write"])
        f = ToolFilter(
            role_def=role, is_background=False,
            background_whitelist=[],
            all_tools=ALL_TOOLS,
        )
        names = {t.name for t in f.visible_tools}
        assert "Write" not in names
        assert "Read" in names

    def test_L4_background_whitelist_restricts(self) -> None:
        role = _make_role(tools=["Read", "Grep", "Write"])
        f = ToolFilter(
            role_def=role, is_background=True,
            background_whitelist=["Read", "Grep"],
            all_tools=ALL_TOOLS,
        )
        names = {t.name for t in f.visible_tools}
        assert names == {"Read", "Grep"}
        # task 不在白名单 → 不出现
        assert "task" not in names

    def test_L1_still_wins_after_L4(self) -> None:
        # 即便 L4 白名单写了 task,L1 仍然 ban
        role = _make_role()
        f = ToolFilter(
            role_def=role, is_background=True,
            background_whitelist=["Read", "task"],
            all_tools=ALL_TOOLS,
        )
        names = {t.name for t in f.visible_tools}
        assert "task" not in names
        assert "Read" in names

    def test_empty_raises_ToolFilterEmptyError(self) -> None:
        role = _make_role(tools=["Read"])
        with pytest.raises(ToolFilterEmptyError) as exc_info:
            ToolFilter(
                role_def=role, is_background=True,
                background_whitelist=[],
                all_tools=ALL_TOOLS,
            ).visible_tools
        # 错误消息含 4 层状态
        assert "L1_global_deny" in str(exc_info.value)
        assert "L4_background_whitelist" in str(exc_info.value)

    def test_no_role_no_filter_passes_through(self) -> None:
        # fork 模式 + 非后台 → 全部工具(除 L1)
        f = ToolFilter(
            role_def=None, is_background=False,
            background_whitelist=[],
            all_tools=ALL_TOOLS,
        )
        names = {t.name for t in f.visible_tools}
        assert "task" not in names
        assert "Read" in names
        assert "Write" in names
        assert "Bash" in names

    def test_GLOBAL_DENY_contains_task(self) -> None:
        # 反向防线:任何破坏 GLOBAL_DENY 的改动都会让这个测试挂掉
        assert "task" in GLOBAL_DENY


# ---- TASK_TOOL / task_executor / SubAgentManager 单元测试 ----


def _build_minimal_config(tools: list[str] | None = None):
    """构造一个最小可用 AppConfig — 没有 subagents 块时走默认。"""
    from baozicode.config.schema import AppConfig, BackendConfig

    if tools is None:
        tools = ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebFetch"]
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="claude-haiku-4-5-20251001"),
        openai=BackendConfig(api_key="x", model="gpt-4o"),
        minimax=BackendConfig(api_key="x", model="minimax"),
        deepseek=BackendConfig(api_key="x", model="deepseek-chat"),
    )
    cfg._test_tools = tools  # 给 manager 校验用
    return cfg


def _make_role_def(name: str = "explorer") -> AgentDef:
    return AgentDef(
        frontmatter=AgentFrontmatter(
            name=name,
            description="test role",
            tools=["Read", "Grep"],
            model="haiku",
            max_iterations=5,
            permission_mode="permissive",
        ),
        body="You are explorer",
        source="builtin",
        path=Path(f"/tmp/{name}"),
    )


class _MockLLM:
    """最简 LLM stub — 不真发请求,只在测试 context 里被 runtime spawn 用。"""

    async def stream(self, *args, **kwargs):
        if False:
            yield None
        return
        yield  # pragma: no cover


class TestTASKTool:
    def test_task_tool_definition(self) -> None:
        assert TASK_TOOL.name == "task"
        assert TASK_TOOL.tool_type == "internal"
        params = TASK_TOOL.parameters
        assert "type" in params["properties"]
        assert params["properties"]["type"]["enum"] == ["definition", "fork"]
        assert "prompt" in params["required"]

    def test_task_tool_required_fields(self) -> None:
        params = TASK_TOOL.parameters
        assert set(params["required"]) >= {"type", "prompt"}


class TestSubAgentManager:
    """SubAgentManager 的轻量测试 — 走 dispatch / cancel_all / clear_tasks 路径。"""

    def _build_manager(
        self, *, main_conv=None, max_concurrent: int = 5,
    ):
        """构造一个真实 runtime + manager(用 mock LLM,跑不到真 LLM)。"""
        from pathlib import Path as _P
        from baozicode.conversation.manager import ConversationManager
        from baozicode.tools.registry import ToolRegistry

        cfg = _build_minimal_config()
        tool_registry = ToolRegistry()
        reg = AgentRegistry()
        reg._defs["explorer"] = _make_role_def()  # bypass scan

        runtime = SubAgentRuntime(
            llm=_MockLLM(),
            hooks=None,
            tool_registry=tool_registry,
            project_root=_P("."),
            config=cfg,
            registry=reg,
        )
        if main_conv is None:
            main_conv = ConversationManager()
        manager = SubAgentManager(
            runtime=runtime,
            main_conversation=main_conv,
            max_concurrent=max_concurrent,
        )
        return manager

    def test_initial_state_empty(self) -> None:
        m = self._build_manager()
        assert m.list_tasks() == []
        assert m.count_by_state() == {}

    def test_dispatch_unknown_role_returns_toolresult_error(self) -> None:
        m = self._build_manager()
        result = m.dispatch(
            type="definition", role="nonexistent", prompt="x", async_=True,
        )
        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "未知 Agent role" in result.content

    def test_dispatch_max_concurrent_returns_error(self) -> None:
        m = self._build_manager(max_concurrent=1)
        # 直接塞一个 running task(不走真 spawn)
        t = TaskInfo(task_id="t-1", type="definition", role="explorer", prompt="x")
        t.state = "running"
        m._tasks["t-1"] = t
        # 再派一个 — 超限
        result = m.dispatch(
            type="definition", role="explorer", prompt="y", async_=True,
        )
        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "并发上限" in result.content

    def test_dispatch_fork_forces_async(self) -> None:
        """fork + async=False → 自动转 async(D8)。"""
        m = self._build_manager()
        # fork 模式 + 无 parent_agent → ValueError(manager 包装成 ToolResult)
        result = m.dispatch(
            type="fork", role=None, prompt="x", async_=False,
            parent_conversation=None, parent_denied_counts=None,
            parent_agent=None,
        )
        # 强制 async 后会进入 spawn 路径,因 parent_messages 缺 ValueError
        assert isinstance(result, ToolResult)
        assert result.is_error
        assert "fork 模式 parent_messages 必填" in result.content

    def test_drain_pending_notifications_only_terminal(self) -> None:
        m = self._build_manager()
        t1 = TaskInfo(task_id="a", type="definition", role="explorer", prompt="x")
        t1.state = "done"
        t1.notification_pending = True
        t2 = TaskInfo(task_id="b", type="definition", role="explorer", prompt="y")
        t2.state = "running"
        t2.notification_pending = True
        m._tasks["a"] = t1
        m._tasks["b"] = t2
        drained = m.drain_pending_notifications()
        ids = {t.task_id for t in drained}
        assert ids == {"a"}
        assert m._tasks["b"].notification_pending is True
        assert m._tasks["a"].notification_pending is False

    def test_cancel_all_sets_running_cancel_events(self) -> None:
        m = self._build_manager()
        t1 = TaskInfo(task_id="a", type="definition", role="explorer", prompt="x")
        t1.state = "running"
        t2 = TaskInfo(task_id="b", type="definition", role="explorer", prompt="y")
        t2.state = "done"
        m._tasks["a"] = t1
        m._tasks["b"] = t2
        m.cancel_all()
        assert t1.cancel_event.is_set()
        assert not t2.cancel_event.is_set()  # done 不被 cancel

    def test_clear_tasks_empties_registry(self) -> None:
        m = self._build_manager()
        t = TaskInfo(task_id="a", type="definition", role="explorer", prompt="x")
        t.state = "running"
        m._tasks["a"] = t
        m.clear_tasks()
        assert m.list_tasks() == []

    def test_demote_to_background(self) -> None:
        m = self._build_manager()
        t = TaskInfo(task_id="a", type="definition", role="explorer", prompt="x")
        t.state = "running"
        m._tasks["a"] = t
        ok = m.demote_to_background("a")
        assert ok
        assert t.cancel_event.is_set()
        # 第二次 demote 已变 timeout,返回 False
        t.state = "timeout"
        assert not m.demote_to_background("a")

    def test_task_id_format(self) -> None:
        m = self._build_manager()
        tid = m._new_task_id()
        assert tid.startswith("task-")
        # 形如 task-20260709-123456-abcd
        parts = tid.split("-")
        assert len(parts) == 4
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 6  # HHMMSS
        assert len(parts[3]) == 4  # xxxx

    def test_count_by_state(self) -> None:
        m = self._build_manager()
        for i, state in enumerate(["running", "running", "done", "failed"]):
            t = TaskInfo(
                task_id=f"t-{i}", type="definition", role="x", prompt="x",
            )
            t.state = state  # type: ignore[assignment]
            m._tasks[t.task_id] = t
        counts = m.count_by_state()
        assert counts == {"running": 2, "done": 1, "failed": 1}

    def test_tool_filter_empty_returns_error(self) -> None:
        """role.tools 与 background_whitelist 交集空 → ToolFilterEmptyError
        → ToolResult(is_error=True)"""
        from pathlib import Path as _P
        from baozicode.conversation.manager import ConversationManager
        from baozicode.tools.registry import ToolRegistry

        cfg = _build_minimal_config()
        tool_registry = ToolRegistry()
        reg = AgentRegistry()
        reg._defs["narrow"] = AgentDef(
            frontmatter=AgentFrontmatter(
                name="narrow", description="x", tools=["Read"],
            ),
            body="", source="builtin", path=_P("/tmp/x"),
        )
        runtime = SubAgentRuntime(
            llm=_MockLLM(), hooks=None, tool_registry=tool_registry,
            project_root=_P("."), config=cfg, registry=reg,
        )
        # v1.2 还没把 subagents 加到 AppConfig 时(runtime 走 fallback 默认),
        # background_whitelist 是 hard-coded 默认 → 含 Read。
        # 我们把 runtime 的 _config 改成 None subagents, 但 forced background,
        # background_whitelist 改不了。直接构造 background=True + role.tools=[Nonexistent]
        # 来强制空集 — 即把 role.tools 设成 ['NoSuchTool']
        reg._defs["bad"] = AgentDef(
            frontmatter=AgentFrontmatter(
                name="bad", description="x", tools=["NoSuchTool"],
            ),
            body="", source="builtin", path=_P("/tmp/x"),
        )
        manager = SubAgentManager(
            runtime=runtime, main_conversation=ConversationManager(),
        )
        result = manager.dispatch(
            type="definition", role="bad", prompt="x", async_=True,
        )
        assert isinstance(result, ToolResult)
        assert result.is_error
        # ToolFilterEmptyError → "工具过滤后空集"
        assert "工具过滤后空集" in result.content or "引用不存在的 tool" in result.content


# ---- SubAgentCard widget tests ----


class TestSubAgentCardWidget:
    """v1.2 TUI — SubAgentCard widget 渲染 / refresh / expand 测试。

    不依赖 Textual Pilot,直接构造 widget + 调 render(),验证渲染文本内容;
    Static.update() 内部要求挂到 app 才能 render,所以这里用 app-less 的
    `__render_body` 方法(若不存在则通过 public `render()` 截取 output)。

    为了让 widget 真能 render,这里挂到 `textual.app.App` 的临时实例。
    """

    def _make_task(
        self,
        task_id: str = "task-001",
        role: str | None = "reviewer",
        type: str = "definition",  # noqa: A002
        state: str = "running",
        last_text: str = "",
    ) -> "TaskInfo":
        from datetime import datetime, timezone

        from baozicode.agents.manager import TaskState  # noqa: F401
        return TaskInfo(
            task_id=task_id,
            type=type,  # type: ignore[arg-type]
            role=role,
            prompt="x",
            state=state,  # type: ignore[arg-type]
            created_at=datetime.now(tz=timezone.utc),
            last_text=last_text,
        )

    def test_first_line_preview_short(self) -> None:
        from baozicode.tui.subagent_card import _first_line_preview
        assert _first_line_preview("hello") == "hello"

    def test_first_line_preview_truncates(self) -> None:
        from baozicode.tui.subagent_card import _first_line_preview
        long = "x" * 200
        out = _first_line_preview(long, n=10)
        assert len(out) == 10
        assert out.endswith("…")

    def test_first_line_preview_splits_newline(self) -> None:
        from baozicode.tui.subagent_card import _first_line_preview
        out = _first_line_preview("first\nsecond line")
        assert out == "first"

    def test_first_line_preview_empty(self) -> None:
        from baozicode.tui.subagent_card import _first_line_preview
        assert _first_line_preview("") == ""

    def test_collapsed_render_shows_role_state(self) -> None:
        """挂到临时 app 验证 widget 渲染 collapsed 时含 role + state。"""
        from textual.app import App

        from baozicode.tui.subagent_card import SubAgentCard

        async def _run() -> None:
            app = App()
            async with app.run_test() as pilot:
                card = SubAgentCard(
                    task_id="t1", role_label="reviewer", type_label="definition",
                )
                await pilot.app.mount(card)
                card.update_from_task(
                    self._make_task(
                        task_id="t1", role="reviewer", state="running",
                        last_text="working on it",
                    )
                )
                rendered = card.render()
                text = str(rendered)
                assert "reviewer" in text
                assert "running" in text
                assert "working on it" in text
                assert "Enter 展开" in text

        import asyncio
        asyncio.run(_run())

    def test_expanded_render_shows_full_text(self) -> None:
        from textual.app import App

        from baozicode.tui.subagent_card import SubAgentCard

        async def _run() -> None:
            app = App()
            async with app.mount_test() as pilot:  # type: ignore[attr-defined]
                pass
        # textual 8 之前用 mount_test,之后改 app.run_test().mount()。Try the simple route.
        async def _run2() -> None:
            a = App()
            async with a.run_test() as pilot:
                card = SubAgentCard(
                    task_id="t2",
                    role_label="reviewer",
                    type_label="fork",
                )
                await pilot.app.mount(card)
                card.toggle_expanded()
                card.update_from_task(
                    self._make_task(
                        task_id="t2",
                        role="reviewer",
                        type="fork",
                        state="running",
                        last_text="first line\n\nsecond line\nthird line",
                    )
                )
                rendered = card.render()
                text = str(rendered)
                assert "first line" in text
                assert "second line" in text
                assert "third line" in text
                assert "Enter 折叠" in text

        import asyncio
        asyncio.run(_run2())

    def test_terminal_state_marks_terminal_class(self) -> None:
        from textual.app import App

        from baozicode.tui.subagent_card import SubAgentCard

        async def _run() -> None:
            a = App()
            async with a.run_test() as pilot:
                card = SubAgentCard(
                    task_id="t3",
                    role_label="reviewer",
                    type_label="definition",
                )
                await pilot.app.mount(card)
                card.update_from_task(
                    self._make_task(task_id="t3", state="done", last_text="ok")
                )
                assert card.has_class("-terminal") is True
                assert card.has_class("-failed") is False

        import asyncio
        asyncio.run(_run())

    def test_failed_state_marks_failed_class(self) -> None:
        from textual.app import App

        from baozicode.tui.subagent_card import SubAgentCard

        async def _run() -> None:
            a = App()
            async with a.run_test() as pilot:
                card = SubAgentCard(
                    task_id="t4",
                    role_label="reviewer",
                    type_label="definition",
                )
                await pilot.app.mount(card)
                card.update_from_task(
                    self._make_task(
                        task_id="t4", state="failed", last_text="boom"
                    )
                )
                assert card.has_class("-failed") is True

        import asyncio
        asyncio.run(_run())


# ---- plugin loader tests ----


class TestFetchPluginAgents:
    """v1.2 MCP plugin loader — `agents://list` + `agents://<name>` 协议。

    用桩 McpSession 和桩 manager 模拟:
    - 单 server + 单 agent → 成功
    - 单 server + 多 agent → 都成功
    - list 端点非 JSON → 该 server 抛错,记 scan_error
    - catalog 不是 list → 该 server 抛错,记 scan_error
    - 某 agent 详情拉取失败 → 跳过该 agent,其他正常
    - manager 是 None → 返回 ([], [])
    - 未连接的 server → 跳过
    """

    @pytest.mark.asyncio
    async def test_none_manager_returns_empty(self) -> None:
        from baozicode.agents.plugin import fetch_plugin_agents
        defs, errors = await fetch_plugin_agents(None)
        assert defs == []
        assert errors == []

    @pytest.mark.asyncio
    async def test_single_server_single_agent(self) -> None:
        from baozicode.agents.plugin import fetch_plugin_agents

        class _StubSession:
            async def read_resource(self, uri):
                if uri == "agents://list":
                    return {
                        "contents": [
                            {"text": json.dumps([
                                {"name": "alpha", "description": "test", "version": "1"}
                            ])}
                        ]
                    }
                if uri == "agents://alpha":
                    return {"contents": [{"text": (
                        "---\nname: alpha\ndescription: test\n---\nalpha body"
                    )}]}
                return {"contents": []}

        class _StubState:
            status = "connected"
            session = _StubSession()

        class _StubManager:
            states = {"server1": _StubState()}

        defs, errors = await fetch_plugin_agents(_StubManager())  # type: ignore[arg-type]
        assert len(defs) == 1
        assert defs[0].frontmatter.name == "alpha"
        assert defs[0].body == "alpha body"
        assert defs[0].source == "plugin"
        assert errors == []

    @pytest.mark.asyncio
    async def test_multi_agents_one_session(self) -> None:
        from baozicode.agents.plugin import fetch_plugin_agents

        class _StubSession:
            async def read_resource(self, uri):
                if uri == "agents://list":
                    return {"contents": [{"text": json.dumps([
                        {"name": "a", "description": "A", "version": "1"},
                        {"name": "b", "description": "B", "version": "1"},
                    ])}]}
                if uri == "agents://a":
                    return {"contents": [{"text": (
                        "---\nname: a\ndescription: A\n---\nA body"
                    )}]}
                if uri == "agents://b":
                    return {"contents": [{"text": (
                        "---\nname: b\ndescription: B\n---\nB body"
                    )}]}
                return {"contents": []}

        class _StubState:
            status = "connected"
            session = _StubSession()

        class _StubManager:
            states = {"srv": _StubState()}

        defs, errors = await fetch_plugin_agents(_StubManager())  # type: ignore[arg-type]
        assert {d.frontmatter.name for d in defs} == {"a", "b"}
        assert all(d.source == "plugin" for d in defs)
        assert errors == []

    @pytest.mark.asyncio
    async def test_list_endpoint_invalid_json_records_error(self) -> None:
        from baozicode.agents.plugin import fetch_plugin_agents

        class _StubSession:
            async def read_resource(self, uri):
                return {"contents": [{"text": "not-json{"}]}

        class _StubState:
            status = "connected"
            session = _StubSession()

        class _StubManager:
            states = {"srv": _StubState()}

        defs, errors = await fetch_plugin_agents(_StubManager())  # type: ignore[arg-type]
        assert defs == []
        assert len(errors) == 1
        assert "agents" in errors[0].reason

    @pytest.mark.asyncio
    async def test_catalog_not_list_records_error(self) -> None:
        from baozicode.agents.plugin import fetch_plugin_agents

        class _StubSession:
            async def read_resource(self, uri):
                return {"contents": [{"text": json.dumps({"x": 1})}]}

        class _StubState:
            status = "connected"
            session = _StubSession()

        class _StubManager:
            states = {"srv": _StubState()}

        defs, errors = await fetch_plugin_agents(_StubManager())  # type: ignore[arg-type]
        assert defs == []
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_one_agent_detail_fails_other_succeeds(self) -> None:
        from baozicode.agents.plugin import fetch_plugin_agents

        class _StubSession:
            async def read_resource(self, uri):
                if uri == "agents://list":
                    return {"contents": [{"text": json.dumps([
                        {"name": "ok", "description": "ok", "version": "1"},
                        {"name": "bad", "description": "bad", "version": "1"},
                    ])}]}
                if uri == "agents://ok":
                    return {"contents": [{"text": (
                        "---\nname: ok\ndescription: ok\n---\nok body"
                    )}]}
                if uri == "agents://bad":
                    raise RuntimeError("network 502")
                return {"contents": []}

        class _StubState:
            status = "connected"
            session = _StubSession()

        class _StubManager:
            states = {"srv": _StubState()}

        defs, errors = await fetch_plugin_agents(_StubManager())  # type: ignore[arg-type]
        # bad 被跳过,不阻断 ok
        assert {d.frontmatter.name for d in defs} == {"ok"}
        assert errors == []  # 单个 agent 失败不上升到 server 级 error

    @pytest.mark.asyncio
    async def test_disconnected_server_skipped(self) -> None:
        from baozicode.agents.plugin import fetch_plugin_agents

        class _StubState:
            status = "failed"  # not connected
            session = None

        class _StubManager:
            states = {"srv": _StubState()}

        defs, errors = await fetch_plugin_agents(_StubManager())  # type: ignore[arg-type]
        assert defs == []
        assert errors == []

    @pytest.mark.asyncio
    async def test_name_mismatch_skipped(self) -> None:
        """server 端给的 name 与详情 frontmatter name 不一致 → 跳过。"""
        from baozicode.agents.plugin import fetch_plugin_agents

        class _StubSession:
            async def read_resource(self, uri):
                if uri == "agents://list":
                    return {"contents": [{"text": json.dumps([
                        {"name": "claimed", "description": "X", "version": "1"},
                    ])}]}
                if uri == "agents://claimed":
                    # frontmatter 名是 actual,与 claimed 不一致
                    return {"contents": [{"text": (
                        "---\nname: actual\ndescription: X\n---\nbody"
                    )}]}
                return {"contents": []}

        class _StubState:
            status = "connected"
            session = _StubSession()

        class _StubManager:
            states = {"srv": _StubState()}

        defs, errors = await fetch_plugin_agents(_StubManager())  # type: ignore[arg-type]
        assert defs == []
        assert errors == []


# ---- last_text 增量累积 ----


class TestSubAgentTaskLastText:
    """TaskInfo.last_text 在 sub-Agent 运行中被增量更新 — TUI 卡片轮询读这个。

    这里用 SubAgentManager._run_subagent 内部的事件循环桩,验证 last_text 写入正确。
    完整运行时测试需要真实 LLM,这里只验证 last_text 字段行为。
    """

    def test_default_last_text_empty(self) -> None:
        from baozicode.agents.manager import TaskInfo
        from datetime import datetime, timezone
        t = TaskInfo(
            task_id="t1", type="definition", role="r",
            prompt="x",
            created_at=datetime.now(tz=timezone.utc),
        )
        assert t.last_text == ""

    def test_role_label_definition(self) -> None:
        from baozicode.agents.manager import TaskInfo
        from datetime import datetime, timezone
        t = TaskInfo(
            task_id="t1", type="definition", role="reviewer",
            prompt="x",
            created_at=datetime.now(tz=timezone.utc),
        )
        assert t.role_label == "reviewer"

    def test_role_label_fork(self) -> None:
        from baozicode.agents.manager import TaskInfo
        from datetime import datetime, timezone
        t = TaskInfo(
            task_id="t1", type="fork", role=None,
            prompt="x",
            created_at=datetime.now(tz=timezone.utc),
        )
        assert t.role_label == "fork"
