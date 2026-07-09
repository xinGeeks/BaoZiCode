"""v1.0 Skills — chat_screen bootstrap helper。

公开 API:
- `bootstrap_skills(project_root, *, tool_registry) -> SkillSet`

`SkillSet` 包含 4 个协作者(registry / activation / loader / executor)
+ 一个 `build_skill_filter(tool_registry)` 工厂(给 Agent 注入 L2 白名单)。

设计取舍:
- 不在 `baozicode/skills/` 内部 import textual / app —— App 层自己 import 本模块。
- 路径解析(builtin_dir / user_dir / project_dir)在内部完成,失败优雅降级到空集合。
- 启动期 L1 校验(None 命名空间 → 全允许)在 SkillLoader.load_skill 触发;
  这里只做轻量 bind_scan_errors + emit 行为,错误的 SkillFrontmatter 已 by-skill
  skip,不影响其它 Skill 加载。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .activation import SkillActivation
from .loader import SkillLoader
from .registry import SkillRegistry, emit_scan_warnings
from .execution import SkillExecutor
from .whitelist import SkillWhitelistFilter

if TYPE_CHECKING:
    from .schema import SkillDef
    from baozicode.tools.registry import ToolRegistry

_log = logging.getLogger(__name__)

__all__ = ["SkillSet", "bootstrap_skills"]


@dataclass(frozen=True)
class SkillSet:
    """Skill 生态聚合对象 — 一次性 bootstrap,四个组件 + 过滤器工厂。"""

    registry: SkillRegistry
    activation: SkillActivation
    loader: SkillLoader
    executor: SkillExecutor

    def build_skill_filter(self, tool_registry: "ToolRegistry") -> SkillWhitelistFilter:
        """返回 L2 白名单守卫,给 Agent.__init__ 用。"""
        return SkillWhitelistFilter(self.activation, tool_registry)


def _default_user_dir() -> Path:
    """`~/.config/baozicode/skills/` —— Skill v1.0 user 级覆盖。"""
    return Path.home() / ".config" / "baozicode" / "skills"


def _default_project_dir(project_root: Path) -> Path:
    """`<project>/.baozicode/skills/` —— project 级最高优先级。"""
    return project_root / ".baozicode" / "skills"


def _builtin_dir() -> Path:
    """包内 `baozicode/skills/builtin/` ——
    总是存在(`commit` / `review` / `test` 三个样板)。"""
    return Path(__file__).parent / "builtin"


def _build_independent_runner(
    loader: SkillLoader, executor: SkillExecutor
):
    """构造独立模式触发器 `(SkillDef, args) -> summary`。

    实际 sub-Agent 编排由 chat_screen 层完成(它能拿到 LLM client + Agent 工厂)。
    测试 / 简化场景下这个 runner 也可被替换。
    """

    async def runner(sd: "SkillDef", args):
        # 默认实现:直接委派给 SkillExecutor(本身会调 load_skill,然后
        # 调用独立 runner 子流程)。生产代码可以覆盖此工厂换更精细实现。
        result = await executor.execute(sd.name, args)
        return result.raw_output or ""

    return runner


def bootstrap_skills(
    project_root: Path | None = None,
    *,
    tool_registry: "ToolRegistry | None" = None,
    builtin_dir: Path | None = None,
    user_dir: Path | None = None,
    project_dir: Path | None = None,
    skills_config: "Any | None" = None,
) -> SkillSet:
    """启动 Skill 生态(由 App.__init__ / 测试调用)。

    流程:
    1. 扫描三级目录(builtin / user / project),构造 SkillRegistry
    2. 构造 SkillActivation,持有 v0.9 CommandRegistry(reuse app._command_registry)
    3. 构造 SkillLoader,持有 SkillRegistry + SkillActivation + 可选 ToolRegistry
    4. 构造 SkillExecutor(独立 runner 由 chat_screen 在运行时提供;此处用 stub)
    5. (P8)ToolRegistry 可用 → 注册 load_skill tool

    Args:
        project_root: 项目根(用于解析 `<root>/.baozicode/skills/`);None 时用 cwd
        tool_registry: 当前 ToolRegistry;为 None 时跳过 load_skill 注册
        builtin_dir: 默认 `<pkg>/skills/builtin`
        user_dir: 默认 `~/.config/baozicode/skills`
        project_dir: 默认 `<project_root>/.baozicode/skills`
        skills_config: v1.0 `SkillsConfig` 实例;为 None 时所有路径走 kwargs / 默认。
            提供后,`enabled=False` 立即返回空 SkillSet(占位)。

    Returns:
        SkillSet — App 持有它,然后做后续独立 runner 注入与 tool 注册。
    """
    # 提前 import CommandRegistry(占位路径也用)
    from baozicode.commands.registry import CommandRegistry

    # v1.0:enabled=False 走占位路径(返回空 SkillSet,保留 app.skills 字段)
    if skills_config is not None and not getattr(skills_config, "enabled", True):
        empty_registry = SkillRegistry()
        empty_activation = SkillActivation(CommandRegistry())
        empty_loader = SkillLoader(empty_registry, empty_activation)
        empty_executor = SkillExecutor(empty_loader, empty_activation, subagent_manager=None)
        return SkillSet(
            registry=empty_registry,
            activation=empty_activation,
            loader=empty_loader,
            executor=empty_executor,
        )

    # 路径解析 — SkillsConfig 字段优先,然后 kwargs,最后默认
    if skills_config is not None:
        if builtin_dir is None:
            builtin_dir = getattr(skills_config, "builtin_dir", None)
        if user_dir is None:
            user_dir = getattr(skills_config, "user_dir", None)
        if project_dir is None:
            project_dir = getattr(skills_config, "project_dir", None)
    if builtin_dir is None:
        builtin_dir = _builtin_dir()
    if user_dir is None:
        user_dir = _default_user_dir()
    if project_dir is None:
        root = project_root if project_root is not None else Path.cwd()
        project_dir = _default_project_dir(root)

    # 1. scan + 合并
    valid_tools = set(tool_registry.get_tool_names()) if tool_registry else None
    registry = SkillRegistry.scan(
        builtin_dir=builtin_dir,
        user_dir=user_dir,
        project_dir=project_dir,
        valid_tools=valid_tools,
    )
    emit_scan_warnings(registry.scan_errors)

    # 2-3. activation + loader(暂时无独立 runner)
    cmd_registry = CommandRegistry()  # bootstrap 时拿不到 app 的;实际 chat_screen 接手注入
    activation = SkillActivation(cmd_registry)
    loader = SkillLoader(
        registry,
        activation,
        tool_registry=tool_registry,
    )
    executor = SkillExecutor(loader, activation, subagent_manager=None)

    return SkillSet(
        registry=registry,
        activation=activation,
        loader=loader,
        executor=executor,
    )
