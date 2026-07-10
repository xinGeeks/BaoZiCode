"""v1.3 Worktree Isolation — SubAgentRuntime.spawn + SubAgentManager
worktree 隔离集成测试。

覆盖 `openspec/changes/v1-3-worktree-isolation/specs/{subagent-manager,
agent-runtime}/spec.md` 的 acceptance scenario。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from baozicode.agents.manager import SubAgentManager
from baozicode.agents.registry import AgentRegistry
from baozicode.agents.runtime import SubAgentRuntime
from baozicode.agents.schema import AgentDef, AgentFrontmatter
from baozicode.worktree import WorktreeInitConfig, WorktreeManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> None:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败 ({proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@x.io")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# test\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


class _MockLLM:
    """最小桩 LLM — 提供 `async def stream`,Agent.run 能进入主循环。

    不真发请求;测试中由 `_run_subagent` 的 cancel_event 走 USER_CANCELLED 路径。
    """

    async def stream(self, *args, **kwargs):  # noqa: ARG002
        # 立刻返回空 → Agent.run 进入「stream 返回」分支,下一拍检测 cancel_event
        if False:
            yield None
        return
        yield  # pragma: no cover


def _minimal_config():
    from baozicode.config.schema import AppConfig, BackendConfig, MemoryConfig

    # 关闭 memory 避免 Agent 构造时在 worktree 写 `.baozicode/memory/MEMORY.md`
    # —— 那样会让 worktree 变成 dirty 干扰 exit() 决策树。
    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="claude-haiku-4-5-20251001"),
        openai=BackendConfig(api_key="x", model="gpt-4o"),
        minimax=BackendConfig(api_key="x", model="minimax"),
        deepseek=BackendConfig(api_key="x", model="deepseek-chat"),
        memory=MemoryConfig(enabled=False),
    )


def _make_runtime(
    project_root: Path,
    registry: AgentRegistry,
    *,
    worktree_manager: WorktreeManager | None = None,
    worktree_init_config: WorktreeInitConfig | None = None,
) -> SubAgentRuntime:
    from baozicode.tools.registry import ToolRegistry
    return SubAgentRuntime(
        llm=_MockLLM(),  # type: ignore[arg-type]
        hooks=None,
        tool_registry=ToolRegistry(),
        project_root=project_root,
        config=_minimal_config(),
        registry=registry,
        worktree_manager=worktree_manager,
        worktree_init_config=worktree_init_config,
    )


def _add_agent_def(
    registry: AgentRegistry,
    *,
    name: str,
    body: str = "test body",
    isolation: str | None = None,
) -> AgentDef:
    fm_kwargs = {"name": name, "description": f"test {name}"}
    if isolation is not None:
        fm_kwargs["isolation"] = isolation  # type: ignore[arg-type]
    fm = AgentFrontmatter(**fm_kwargs)
    agent_def = AgentDef(
        frontmatter=fm,
        body=body,
        source="builtin",
        path=Path(f"/tmp/{name}/AGENT.md"),
    )
    registry._defs[name] = agent_def
    return agent_def


# ---------------------------------------------------------------------------
# SubAgentRuntime.spawn — worktree 隔离分支
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestRuntimeWorktreeBranch:
    """`SubAgentRuntime.spawn` 处理 `isolation="worktree"` 的 4 类场景。"""

    @pytest.mark.asyncio
    async def test_definition_with_isolation_creates_worktree(
        self, git_repo: Path,
    ) -> None:
        reg = AgentRegistry()
        _add_agent_def(reg, name="api-designer", isolation="worktree")
        wm = WorktreeManager(setup_dir=git_repo)
        cfg = WorktreeInitConfig()
        runtime = _make_runtime(
            git_repo, reg, worktree_manager=wm, worktree_init_config=cfg,
        )

        agent = await runtime.spawn(
            task_id="t-1",
            type="definition",
            role_def=reg._defs["api-designer"],
            prompt="build the api",
            parent_messages=None,
            parent_denied_counts=None,
            parent_agent=None,
            is_background=True,
        )

        # worktree 已建
        wt_path = git_repo / ".worktrees" / "api-designer"
        assert wt_path.exists()
        assert (wt_path / ".git").exists()
        # Agent 上挂的 state
        assert getattr(agent, "_worktree_name", None) == "api-designer"
        assert getattr(agent, "_worktree_state", None) == "active"
        # effective project_root(走 _subagent_meta,因为 Agent 不把
        # project_root 暴露成属性 —— 只是构造时传给 PromptBuilder /
        # memory bootstrap 用)
        meta = agent._subagent_meta  # type: ignore[attr-defined]
        assert meta["worktree_name"] == "api-designer"
        assert meta["worktree_state"] == "active"
        assert meta["effective_project_root"] is not None
        assert Path(meta["effective_project_root"]).resolve() == wt_path.resolve()

        # 清场
        await wm.remove("api-designer", force=True)

    @pytest.mark.asyncio
    async def test_fork_with_isolation_rejected(
        self, git_repo: Path,
    ) -> None:
        """D4:fork + worktree 互斥 → ValueError,不调 worktree_manager。"""
        reg = AgentRegistry()
        _add_agent_def(reg, name="api-designer", isolation="worktree")
        wm = WorktreeManager(setup_dir=git_repo)
        cfg = WorktreeInitConfig()
        runtime = _make_runtime(
            git_repo, reg, worktree_manager=wm, worktree_init_config=cfg,
        )

        # parent_agent 不能 None(已有断言先拦);给一个 mock
        class _StubParent:
            _prompt = object()

        with pytest.raises(ValueError) as exc_info:
            await runtime.spawn(
                task_id="t-1",
                type="fork",
                role_def=reg._defs["api-designer"],
                prompt="fork it",
                parent_messages=[],
                parent_denied_counts=None,
                parent_agent=_StubParent(),
                is_background=True,
            )
        msg = str(exc_info.value)
        assert "fork" in msg.lower() and "worktree" in msg.lower()
        # worktree 没建(互斥先拦)
        assert not (git_repo / ".worktrees" / "api-designer").exists()

    @pytest.mark.asyncio
    async def test_isolation_default_none_no_worktree(
        self, git_repo: Path,
    ) -> None:
        """不写 isolation 字段 → None → 不创建 worktree(等价 v1.2 行为)。"""
        reg = AgentRegistry()
        _add_agent_def(reg, name="plain")  # 默认 isolation=None
        wm = WorktreeManager(setup_dir=git_repo)
        cfg = WorktreeInitConfig()
        runtime = _make_runtime(
            git_repo, reg, worktree_manager=wm, worktree_init_config=cfg,
        )

        agent = await runtime.spawn(
            task_id="t-1",
            type="definition",
            role_def=reg._defs["plain"],
            prompt="plain",
            parent_messages=None,
            parent_denied_counts=None,
            parent_agent=None,
            is_background=True,
        )
        # 没 worktree 元数据
        assert getattr(agent, "_worktree_name", None) is None
        assert getattr(agent, "_worktree_state", None) is None
        # subagent_meta 也标记 effective_project_root = None(等价 v1.2)
        meta = agent._subagent_meta  # type: ignore[attr-defined]
        assert meta["worktree_name"] is None
        assert meta["worktree_state"] is None
        assert meta["effective_project_root"] is None
        # 文件系统没 .worktrees/ 残留
        assert not (git_repo / ".worktrees" / "plain").exists()

    @pytest.mark.asyncio
    async def test_isolation_worktree_without_manager_rejected(
        self, git_repo: Path,
    ) -> None:
        """runtime 没配 worktree_manager 但 role 声明 worktree → ValueError。"""
        reg = AgentRegistry()
        _add_agent_def(reg, name="api-designer", isolation="worktree")
        # 不传 worktree_manager
        runtime = _make_runtime(git_repo, reg)

        with pytest.raises(ValueError) as exc_info:
            await runtime.spawn(
                task_id="t-1",
                type="definition",
                role_def=reg._defs["api-designer"],
                prompt="x",
                parent_messages=None,
                parent_denied_counts=None,
                parent_agent=None,
                is_background=True,
            )
        assert "WorktreeManager" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SubAgentRuntime:worktree + Initializer 串联
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestRuntimeWorktreeInitializer:
    """worktree 创建后 Initializer 跑 4 步(至少 hooks + gitignore 可见)。"""

    @pytest.mark.asyncio
    async def test_initializer_runs_after_create(self, git_repo: Path) -> None:
        reg = AgentRegistry()
        _add_agent_def(reg, name="api", isolation="worktree")
        wm = WorktreeManager(setup_dir=git_repo)
        cfg = WorktreeInitConfig()
        runtime = _make_runtime(
            git_repo, reg, worktree_manager=wm, worktree_init_config=cfg,
        )

        await runtime.spawn(
            task_id="t-1",
            type="definition",
            role_def=reg._defs["api"],
            prompt="x",
            parent_messages=None,
            parent_denied_counts=None,
            parent_agent=None,
            is_background=True,
        )

        wt = git_repo / ".worktrees" / "api"
        # gitignore 加了 .worktrees/
        gi = git_repo / ".gitignore"
        assert gi.exists()
        text = gi.read_text()
        assert ".worktrees" in text
        # hooks 目录创建了(.worktrees/_hooks/)
        hooks_dir = git_repo / ".worktrees" / "_hooks"
        assert hooks_dir.exists()
        assert hooks_dir.is_dir()

        # 清场
        await wm.remove("api", force=True)


# ---------------------------------------------------------------------------
# SubAgentManager — worktree 集成
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestManagerWorktreeIntegration:
    """SubAgentManager 注入 worktree_manager + TaskInfo 字段。"""

    @pytest.mark.asyncio
    async def test_task_info_records_worktree_metadata(
        self, git_repo: Path,
    ) -> None:
        from baozicode.conversation.manager import ConversationManager
        reg = AgentRegistry()
        _add_agent_def(reg, name="api", isolation="worktree")
        wm = WorktreeManager(setup_dir=git_repo)
        runtime = _make_runtime(
            git_repo, reg, worktree_manager=wm,
            worktree_init_config=WorktreeInitConfig(),
        )
        mgr = SubAgentManager(
            runtime=runtime,
            main_conversation=ConversationManager(),
            worktree_manager=wm,
        )

        task_id = await mgr.dispatch(
            type="definition", role="api", prompt="x", async_=True,
        )
        assert isinstance(task_id, str)
        task = mgr._tasks[task_id]
        assert task.worktree_name == "api"
        assert task.worktree_state == "active"

        # cancel + 等 _run_subagent 收尾(state 终态后,finally 跑
        # _handle_worktree_exit → worktree_state 才更新)
        mgr.cancel_all()
        for _ in range(100):
            if task.state in ("canceled", "done", "failed"):
                break
            await asyncio.sleep(0.05)
        # state 终态已到,但 finally 还要走完才更新 worktree_state
        for _ in range(100):
            if task.worktree_state != "active":
                break
            await asyncio.sleep(0.05)
        # done → force=False → fresh worktree 干净 → "removed"
        assert task.worktree_state == "removed"
        assert not (git_repo / ".worktrees" / "api").exists()

    @pytest.mark.asyncio
    async def test_no_worktree_when_isolation_none(
        self, git_repo: Path,
    ) -> None:
        from baozicode.conversation.manager import ConversationManager
        reg = AgentRegistry()
        _add_agent_def(reg, name="plain")  # isolation=None
        wm = WorktreeManager(setup_dir=git_repo)
        runtime = _make_runtime(
            git_repo, reg, worktree_manager=wm,
            worktree_init_config=WorktreeInitConfig(),
        )
        mgr = SubAgentManager(
            runtime=runtime,
            main_conversation=ConversationManager(),
            worktree_manager=wm,
        )

        task_id = await mgr.dispatch(
            type="definition", role="plain", prompt="x", async_=True,
        )
        task = mgr._tasks[task_id]
        assert task.worktree_name is None
        assert task.worktree_state is None
        # cancel + 清场
        mgr.cancel_all()
        await asyncio.sleep(0.2)