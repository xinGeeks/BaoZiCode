"""v1.0/v1.2 Skills — SkillExecutor 双模式执行单元测试。

v1.2 重写:SkillExecutor 不再走 `independent_runner` 回调,而是直接构造
in-memory AgentDef 注册到 SubAgentManager._runtime._registry._defs 然后调
`dispatch(type="definition", role=name, async_=True)`。本测试用桩 SubAgentManager
覆盖两条路径(成功 / 失败 / 无 manager)。

覆盖:
- SkillExecutionResult dataclass + mode 字段
- execute() 模式分发(shared / independent)
- shared 路径 → loader.load_skill + 返回 LoadSkillResult 摘要
- independent 路径 → SubAgentManager stub 返回 text → 包成 SkillExecutionResult
- independent 缺 manager → ok=False 但 summary 含诊断
- SkillLoader.execute 走 executor(独立模式 OK)+ 不走 executor(降级)
- SkillActivation 斜杠对独立 Skill:有 independent_invoke → 走 invoke;
  无 → 退回 shared 行为(返回 body)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from baozicode.commands.registry import CommandRegistry
from baozicode.skills.activation import SkillActivation
from baozicode.skills.execution import (
    SkillExecutionResult,
    SkillExecutor,
)
from baozicode.skills.loader import SkillLoader
from baozicode.skills.schema import parse_frontmatter


# ---- SubAgentManager 桩 ----


class _StubTask:
    def __init__(
        self,
        *,
        state: str,
        result: str = "",
        error: str | None = None,
    ) -> None:
        self.state = state
        self.result = result
        self.error = error


class _StubRegistry:
    def __init__(self) -> None:
        self._defs: dict[str, Any] = {}


class _StubRuntime:
    def __init__(self) -> None:
        self._registry = _StubRegistry()


class _StubSubAgentManager:
    """最小桩:模拟 SubAgentManager 接口,SkillExecutor 调用。

    `dispatch(...)` 同步返回 task_id(str);`get_task(task_id)` 返回 task 对象
    (SkillExecutor 内部轮询 task.state 到 terminal)。
    """

    def __init__(
        self,
        *,
        result: str = "stub summary text",
        state: str = "done",
        error: str | None = None,
    ) -> None:
        self._runtime = _StubRuntime()
        self._next_id = 0
        self._tasks: dict[str, _StubTask] = {}
        self._result = result
        self._state = state
        self._error = error
        self.dispatched: list[dict[str, Any]] = []

    async def dispatch(  # noqa: A002
        self,
        *,
        type: str,  # noqa: A002
        role: str,
        prompt: str,
        async_: bool = True,
    ) -> str:
        self._next_id += 1
        task_id = f"stub-{self._next_id}"
        self._tasks[task_id] = _StubTask(
            state=self._state, result=self._result, error=self._error,
        )
        self.dispatched.append(
            {"type": type, "role": role, "prompt": prompt, "async_": async_}
        )
        return task_id

    def get_task(self, task_id: str) -> _StubTask | None:
        return self._tasks.get(task_id)


# ---- helpers ----


def _make_sd(text: str):
    fm, body = parse_frontmatter(text, file_path=Path("/tmp/x.md"))

    class _Stub:
        def __init__(self):
            self._fm = fm
            self.body = body
            self.frontmatter = fm
            self.name = fm.name
            self.description = fm.description
            self.mode = fm.mode
            self.allowed_tools = list(fm.allowed_tools or [])
            self.history_bubbles = fm.history_bubbles
            self.model = fm.model
            self.hidden = fm.hidden

    return _Stub()


class _StubRegistrySkill:
    def __init__(self, sd):
        self._sd = sd

    def lookup(self, name):
        if self._sd and self._sd.name == name:
            return self._sd
        return None


def _loader_pair(sd):
    """构造 (SkillLoader, SkillActivation) 对。"""
    activation = SkillActivation(CommandRegistry())
    loader = SkillLoader(_StubRegistrySkill(sd), activation)
    return loader, activation


def _make_executor(
    loader,
    activation,
    *,
    manager: _StubSubAgentManager | None = None,
):
    return SkillExecutor(loader, activation, subagent_manager=manager)


# ---- SkillExecutionResult ----


class TestResultDataclass:
    def test_ok_path(self) -> None:
        r = SkillExecutionResult(
            ok=True, name="x", mode="shared", summary="done"
        )
        assert r.ok is True
        assert r.name == "x"
        assert r.mode == "shared"
        assert r.summary == "done"
        assert r.raw_output is None

    def test_independent_carries_raw_output(self) -> None:
        r = SkillExecutionResult(
            ok=True,
            name="review",
            mode="independent",
            summary="[review 摘要] x",
            raw_output="x",
        )
        assert r.raw_output == "x"


# ---- shared 路径 ----


class TestSharedMode:
    def _shared_sd(self):
        return _make_sd(
            "---\nname: commit\ndescription: d\n---\nsop body"
        )

    @pytest.mark.asyncio
    async def test_shared_just_loads(self) -> None:
        sd = self._shared_sd()
        loader, activation = _loader_pair(sd)
        executor = _make_executor(loader, activation)
        result = await executor.execute("commit")
        assert result.ok is True
        assert result.mode == "shared"
        assert "commit" in result.summary
        assert result.raw_output is None
        assert activation.is_active("commit") is True


# ---- independent 路径 ----


class TestIndependentMode:
    def _ind_sd(self):
        return _make_sd(
            "---\n"
            "name: review\n"
            "description: d\n"
            "mode: independent\n"
            "history-bubbles: 3\n"
            "---\nreview sop"
        )

    @pytest.mark.asyncio
    async def test_independent_dispatches_and_returns(self) -> None:
        sd = self._ind_sd()
        loader, activation = _loader_pair(sd)
        manager = _StubSubAgentManager(result="stub summary text")
        executor = _make_executor(loader, activation, manager=manager)
        result = await executor.execute("review")
        assert result.ok is True
        assert result.mode == "independent"
        assert result.summary.startswith("[review 子对话摘要]")
        assert "stub summary text" in result.summary
        assert result.raw_output == "stub summary text"
        assert activation.is_active("review") is True
        # dispatch 调用记录
        assert len(manager.dispatched) == 1
        assert manager.dispatched[0]["type"] == "definition"
        assert manager.dispatched[0]["role"] == "review"
        assert manager.dispatched[0]["async_"] is True

    @pytest.mark.asyncio
    async def test_independent_no_manager_fails(self) -> None:
        sd = self._ind_sd()
        loader, activation = _loader_pair(sd)
        executor = _make_executor(loader, activation, manager=None)
        result = await executor.execute("review")
        assert result.ok is False
        assert result.mode == "independent"
        assert "SubAgentManager" in result.summary

    @pytest.mark.asyncio
    async def test_independent_task_failed_returns_error(self) -> None:
        sd = self._ind_sd()
        loader, activation = _loader_pair(sd)
        manager = _StubSubAgentManager(
            state="failed", error="LLM timeout"
        )
        executor = _make_executor(loader, activation, manager=manager)
        result = await executor.execute("review")
        assert result.ok is False
        assert "LLM timeout" in result.summary

    @pytest.mark.asyncio
    async def test_independent_empty_output(self) -> None:
        sd = self._ind_sd()
        loader, activation = _loader_pair(sd)
        manager = _StubSubAgentManager(result="")
        executor = _make_executor(loader, activation, manager=manager)
        result = await executor.execute("review")
        assert result.ok is True
        assert "无输出" in result.summary
        assert result.raw_output == ""


# ---- SkillLoader.execute 集成 executor ----


class TestLoaderDispatches:
    def _shared_sd(self):
        return _make_sd(
            "---\nname: commit\ndescription: d\n---\nbody"
        )

    def _ind_sd(self):
        return _make_sd(
            "---\n"
            "name: review\n"
            "description: d\n"
            "mode: independent\n"
            "---\nbody"
        )

    @pytest.mark.asyncio
    async def test_loader_dispatches_independent_to_executor(self) -> None:
        sd = self._ind_sd()
        activation = SkillActivation(CommandRegistry())
        loader = SkillLoader(_StubRegistrySkill(sd), activation)
        manager = _StubSubAgentManager(result="summary A")
        executor = _make_executor(loader, activation, manager=manager)
        loader._executor = executor
        result = await loader.execute({"name": "review"})
        assert not result.is_error
        assert "summary A" in result.content
        assert len(manager.dispatched) == 1
        assert manager.dispatched[0]["type"] == "definition"

    @pytest.mark.asyncio
    async def test_loader_falls_back_to_load_skill_when_no_executor(self) -> None:
        sd = self._shared_sd()
        activation = SkillActivation(CommandRegistry())
        loader = SkillLoader(_StubRegistrySkill(sd), activation)
        # executor 未设置 → 退到 load_skill 路径
        result = await loader.execute({"name": "commit"})
        assert not result.is_error
        assert "commit" in result.content


# ---- SkillActivation 斜杠集成 ----


class TestActivationSlashIndependent:
    def _ind_sd(self):
        return _make_sd(
            "---\n"
            "name: review\n"
            "description: d\n"
            "mode: independent\n"
            "---\nbody"
        )

    @pytest.mark.asyncio
    async def test_independent_slash_runs_invoke(self) -> None:
        sd = self._ind_sd()
        activation = SkillActivation(CommandRegistry())
        activation.activate(
            "review",
            sd.body,
            mode="independent",
            skill_def=sd,
        )
        seen: list = []

        async def invoker(sd_, args):
            seen.append(args)
            return "summary text"

        activation._independent_invoke = invoker
        def_ = activation._registry.lookup("review")
        assert def_ is not None
        result = await def_.handler("some args")
        assert seen == [{"0": "some args"}]
        assert "summary text" in result.text

    @pytest.mark.asyncio
    async def test_independent_slash_no_invoke_falls_back_to_body(self) -> None:
        sd = self._ind_sd()
        activation = SkillActivation(CommandRegistry())
        activation.activate(
            "review",
            sd.body,
            mode="independent",
            skill_def=sd,
        )
        def_ = activation._registry.lookup("review")
        assert def_ is not None
        result = await def_.handler()
        assert result.text == sd.body

    @pytest.mark.asyncio
    async def test_shared_slash_always_returns_body(self) -> None:
        sd = _make_sd(
            "---\nname: commit\ndescription: d\n---\nsop body"
        )
        activation = SkillActivation(CommandRegistry())
        activation.activate(
            "commit",
            sd.body,
            mode="shared",
        )

        async def invoker(sd_, args):
            return "should not be called"

        activation._independent_invoke = invoker
        def_ = activation._registry.lookup("commit")
        assert def_ is not None
        result = await def_.handler()
        assert result.text == "sop body"

    @pytest.mark.asyncio
    async def test_independent_invoke_raises_handled(self) -> None:
        sd = self._ind_sd()
        activation = SkillActivation(CommandRegistry())
        activation.activate(
            "review",
            sd.body,
            mode="independent",
            skill_def=sd,
        )

        async def invoker(sd_, args):
            raise RuntimeError("LLM 502")

        activation._independent_invoke = invoker
        def_ = activation._registry.lookup("review")
        assert def_ is not None
        result = await def_.handler()
        assert "LLM 502" in result.text
        assert "失败" in result.text
