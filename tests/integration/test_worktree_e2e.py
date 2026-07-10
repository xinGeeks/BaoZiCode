"""v1.3 Worktree Isolation — 端到端集成测试。

覆盖 `openspec/changes/v1-3-worktree-isolation/tasks.md` 13.1:

在 fixture git repo 里跑一个 `isolation: worktree` 的 definition sub-Agent
(用脚本化 LLM 驱动真实 `Write` 工具),验证:

- worktree 目录在 `.worktrees/<name>/` 创建
- sub-Agent 写的文件**只**落在 worktree,**不**出现在主 repo
- sub-Agent 跑完无变更 → worktree 自动清掉(exit 决策树 clean → removed)
- 有未提交改动 → 保留 `.worktrees/<name>/` + `worktree_state == "detached"`
  (TUI 卡片可见)

这条链路是真 e2e:dispatch → spawn → WorktreeManager.create →
Initializer → Agent.run(真跑 Write 工具)→ _handle_worktree_exit。
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
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import ToolRegistry
from baozicode.worktree import WorktreeInitConfig, WorktreeManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> None:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)
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


class _ScriptedLLM:
    """按 turn 顺序驱动 Agent — 每次 `stream()` 消费一个 turn。

    turn 为空 / 脚本耗尽 → yield 空 → Agent.run 视为本轮无 tool_use → COMPLETED。
    """

    def __init__(self, turns: list[list[ContentDelta]]) -> None:
        self._turns = list(turns)
        self._i = 0

    async def stream(self, messages, system=None, tools=None, *, cache_breakpoints=None):  # noqa: ANN001, ARG002
        if self._i < len(self._turns):
            turn = self._turns[self._i]
            self._i += 1
            for delta in turn:
                yield delta
        return


def _e2e_config():
    """permissive 模式(Write 是 side_effect,fallthrough → allow,无需 L5),
    memory 关闭(否则 Agent 构造时在 worktree 写 MEMORY.md 污染 dirty 判定),
    background_whitelist 放开 Write(后台 sub-Agent 默认只读)。
    """
    from baozicode.config.schema import (
        AppConfig,
        BackendConfig,
        MemoryConfig,
        PermissionsV5,
        SubAgentsConfig,
        WorktreeConfig,
    )

    return AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="claude-haiku-4-5-20251001"),
        openai=BackendConfig(api_key="x", model="gpt-4o"),
        minimax=BackendConfig(api_key="x", model="minimax"),
        deepseek=BackendConfig(api_key="x", model="deepseek-chat"),
        memory=MemoryConfig(enabled=False),
        permissions_v5=PermissionsV5(mode="permissive"),
        subagents=SubAgentsConfig(
            background_whitelist=[
                "Read", "Write", "Grep", "Glob", "notify_complete",
            ],
            worktree=WorktreeConfig(enabled=True),
        ),
    )


def _add_isolated_role(registry: AgentRegistry, name: str) -> AgentDef:
    fm = AgentFrontmatter(
        name=name, description=f"e2e {name}", isolation="worktree",
    )
    agent_def = AgentDef(
        frontmatter=fm,
        body="you write one file then finish",
        source="builtin",
        path=Path(f"/tmp/{name}/AGENT.md"),
    )
    registry._defs[name] = agent_def
    return agent_def


def _make_manager(
    git_repo: Path,
    registry: AgentRegistry,
    llm: _ScriptedLLM,
    wm: WorktreeManager,
) -> SubAgentManager:
    runtime = SubAgentRuntime(
        llm=llm,  # type: ignore[arg-type]
        hooks=None,
        tool_registry=ToolRegistry(),
        project_root=git_repo,
        config=_e2e_config(),
        registry=registry,
        worktree_manager=wm,
        worktree_init_config=WorktreeInitConfig(),
    )
    return SubAgentManager(
        runtime=runtime,
        main_conversation=ConversationManager(),
        worktree_manager=wm,
    )


async def _wait_terminal(task, timeout: float = 8.0) -> None:
    """等 task 到终态 + worktree_state 定型(finally 里 exit 决策跑完)。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if task.state in ("done", "failed", "canceled", "timeout"):
            break
        await asyncio.sleep(0.02)
    # worktree_state 在 finally 的 _handle_worktree_exit 里从 "active" 更新
    while asyncio.get_event_loop().time() < deadline:
        if task.worktree_name is None or task.worktree_state != "active":
            break
        await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# 13.1 — 单 sub-Agent 端到端
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("git_repo")
class TestWorktreeE2E:
    @pytest.mark.asyncio
    async def test_write_stays_in_worktree_and_preserved_when_dirty(
        self, git_repo: Path,
    ) -> None:
        """sub-Agent 写文件 → 文件只在 worktree、不在主 repo;dirty → detached 保留。"""
        wt_path = git_repo / ".worktrees" / "writer"
        artifact = wt_path / "artifact.txt"

        reg = AgentRegistry()
        _add_isolated_role(reg, "writer")
        wm = WorktreeManager(setup_dir=git_repo)
        llm = _ScriptedLLM([
            [ContentDelta(type="tool_use", text=ToolCall(
                id="c1", name="Write",
                arguments={
                    "file_path": str(artifact),
                    "content": "hello from sub-Agent\n",
                },
            ))],
            [ContentDelta(type="text", text="done")],
        ])
        mgr = _make_manager(git_repo, reg, llm, wm)

        task_id = await mgr.dispatch(
            type="definition", role="writer", prompt="write it", async_=True,
        )
        assert isinstance(task_id, str)
        task = mgr._tasks[task_id]
        await _wait_terminal(task)

        assert task.state == "done"
        # 文件写进了 worktree
        assert artifact.exists()
        assert artifact.read_text() == "hello from sub-Agent\n"
        # 文件**没**出现在主 repo
        assert not (git_repo / "artifact.txt").exists()
        # dirty(untracked artifact.txt)→ exit 决策树保留 detached
        assert task.worktree_state == "detached"
        assert wt_path.exists()

        # 清场
        await wm.remove("writer", force=True)

    @pytest.mark.asyncio
    async def test_clean_subagent_worktree_removed(
        self, git_repo: Path,
    ) -> None:
        """sub-Agent 无文件改动 → worktree 干净 → exit 决策树 removed。"""
        reg = AgentRegistry()
        _add_isolated_role(reg, "noop")
        wm = WorktreeManager(setup_dir=git_repo)
        # 空脚本 → Agent 首轮无 tool_use → COMPLETED,worktree 全干净
        llm = _ScriptedLLM([[ContentDelta(type="text", text="nothing to do")]])
        mgr = _make_manager(git_repo, reg, llm, wm)

        task_id = await mgr.dispatch(
            type="definition", role="noop", prompt="do nothing", async_=True,
        )
        task = mgr._tasks[task_id]
        await _wait_terminal(task)

        assert task.state == "done"
        assert task.worktree_state == "removed"
        assert not (git_repo / ".worktrees" / "noop").exists()

    @pytest.mark.asyncio
    async def test_worktree_dir_created_under_dot_worktrees(
        self, git_repo: Path,
    ) -> None:
        """worktree 目录固定 `.worktrees/<name>/` + 含 `.git` file。"""
        reg = AgentRegistry()
        _add_isolated_role(reg, "probe")
        wm = WorktreeManager(setup_dir=git_repo)
        # 写一个文件让它保留 detached,方便断言目录结构后手动清
        wt_path = git_repo / ".worktrees" / "probe"
        llm = _ScriptedLLM([
            [ContentDelta(type="tool_use", text=ToolCall(
                id="c1", name="Write",
                arguments={"file_path": str(wt_path / "x.txt"), "content": "x"},
            ))],
            [ContentDelta(type="text", text="ok")],
        ])
        mgr = _make_manager(git_repo, reg, llm, wm)

        task_id = await mgr.dispatch(
            type="definition", role="probe", prompt="x", async_=True,
        )
        task = mgr._tasks[task_id]
        await _wait_terminal(task)

        assert wt_path.is_dir()
        assert (wt_path / ".git").is_file()
        # .gitignore 里有 .worktrees/(Initializer step 4)
        assert ".worktrees" in (git_repo / ".gitignore").read_text()

        await wm.remove("probe", force=True)
