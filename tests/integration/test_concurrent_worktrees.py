"""v1.3 Worktree Isolation — 并行 worktree 隔离集成测试。

覆盖 `openspec/changes/v1-3-worktree-isolation/tasks.md` 13.2:

两个并行的 `isolation: worktree` sub-Agent 各写各的文件,互不冲突 ——
每个文件只落在自己的 worktree,不串到对方 worktree,也不落到主 repo。

merge 到主 repo 的策略**不**在 v1.3 范围(交给上层 `git merge`),本测试
只验证「并行写入的物理隔离」这一 v1.3 保证。
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


def _write_then_done(target: Path, content: str) -> _ScriptedLLM:
    return _ScriptedLLM([
        [ContentDelta(type="tool_use", text=ToolCall(
            id="c1", name="Write",
            arguments={"file_path": str(target), "content": content},
        ))],
        [ContentDelta(type="text", text="done")],
    ])


async def _wait_terminal(task, timeout: float = 10.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if task.state in ("done", "failed", "canceled", "timeout"):
            break
        await asyncio.sleep(0.02)
    while asyncio.get_event_loop().time() < deadline:
        if task.worktree_name is None or task.worktree_state != "active":
            break
        await asyncio.sleep(0.02)


@pytest.mark.usefixtures("git_repo")
class TestConcurrentWorktrees:
    @pytest.mark.asyncio
    async def test_two_parallel_subagents_write_isolated_files(
        self, git_repo: Path,
    ) -> None:
        """两个并行 sub-Agent 各写各的 out.txt,互不串,主 repo 干净。"""
        alpha_wt = git_repo / ".worktrees" / "alpha"
        beta_wt = git_repo / ".worktrees" / "beta"
        alpha_file = alpha_wt / "out.txt"
        beta_file = beta_wt / "out.txt"

        # 各自独立 registry + runtime + LLM(共享同一个 WorktreeManager /
        # 同一个 git repo),精确指向自己的 worktree 路径。
        wm = WorktreeManager(setup_dir=git_repo)

        reg_a = AgentRegistry()
        reg_a._defs["alpha"] = AgentDef(
            frontmatter=AgentFrontmatter(
                name="alpha", description="a", isolation="worktree",
            ),
            body="write out.txt", source="builtin",
            path=Path("/tmp/alpha/AGENT.md"),
        )
        reg_b = AgentRegistry()
        reg_b._defs["beta"] = AgentDef(
            frontmatter=AgentFrontmatter(
                name="beta", description="b", isolation="worktree",
            ),
            body="write out.txt", source="builtin",
            path=Path("/tmp/beta/AGENT.md"),
        )

        mgr_a = _make_manager(
            git_repo, reg_a, _write_then_done(alpha_file, "ALPHA\n"), wm,
        )
        mgr_b = _make_manager(
            git_repo, reg_b, _write_then_done(beta_file, "BETA\n"), wm,
        )

        # 并行派发
        tid_a = await mgr_a.dispatch(
            type="definition", role="alpha", prompt="x", async_=True,
        )
        tid_b = await mgr_b.dispatch(
            type="definition", role="beta", prompt="x", async_=True,
        )
        task_a = mgr_a._tasks[tid_a]
        task_b = mgr_b._tasks[tid_b]

        await asyncio.gather(_wait_terminal(task_a), _wait_terminal(task_b))

        assert task_a.state == "done"
        assert task_b.state == "done"

        # 各自的文件落在各自 worktree
        assert alpha_file.read_text() == "ALPHA\n"
        assert beta_file.read_text() == "BETA\n"
        # 不串:alpha 的 worktree 没有 beta 的内容,反之亦然
        assert not (alpha_wt / "out.txt").read_text() == "BETA\n"
        assert not (beta_wt / "out.txt").read_text() == "ALPHA\n"
        # 主 repo 干净:两个 out.txt 都不在主工作树
        assert not (git_repo / "out.txt").exists()
        # 两个都 dirty → detached 保留
        assert task_a.worktree_state == "detached"
        assert task_b.worktree_state == "detached"

        # 清场
        await wm.remove("alpha", force=True)
        await wm.remove("beta", force=True)

    @pytest.mark.asyncio
    async def test_parallel_worktrees_have_distinct_paths(
        self, git_repo: Path,
    ) -> None:
        """两个 worktree 路径不同,list_active 能同时看到两个。"""
        wm = WorktreeManager(setup_dir=git_repo)
        await wm.create("one")
        await wm.create("two")

        active = await wm.list_active()
        names = {p.name for p in active}
        assert {"one", "two"} <= names
        assert (git_repo / ".worktrees" / "one").resolve() != (
            git_repo / ".worktrees" / "two"
        ).resolve()

        await wm.remove("one", force=True)
        await wm.remove("two", force=True)
