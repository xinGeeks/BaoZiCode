"""v1.2 SubAgent Delegation — sub-Agent runtime(state isolation + BuiltPrompt)。

公开 API:
- `SubAgentRuntime.__init__(llm, hooks, tool_registry, project_root, config, registry,
                              *, worktree_manager=None, worktree_init_config=None)`
- `async SubAgentRuntime.spawn(*, task_id, type, role_def, prompt, parent_messages,
                           parent_denied_counts, parent_agent, is_background) -> Agent`

职责:
1. 用 `ToolFilter` 算出 sub-Agent 的可见工具集
2. 构造一个独立的 `Agent` 实例:
   - 全新 `ConversationManager`(archiver=None — sub-Agent 消息不写主 session JSONL)
   - 全新 `MergedPermissions` / `RuleEngine`
   - 全新 `UsageStats`(继承主 session_usage,但每次 run() 单独累积)
3. fork 模式:**共享** parent._prompt 对象(byte-identical cache key)
4. definition 模式:用 role body 作为 identity section,重建一份新的 BuiltPrompt
5. 把 `subagent_meta` dict 注入 Agent 实例,供 `_fire_lifecycle_safe` 加 `subagent` 字段
6. v1.3:若 `role_def.frontmatter.isolation == "worktree"`,创建独立
   git worktree + 注入 `effective_project_root` + 设 `_worktree_state`

共享(只读,sub-Agent 不会改):
- `LLMClient` / `HookDispatcher` / `ToolRegistry` 全局 / `AppConfig`
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

from baozicode.agents.filter import ToolFilter
from baozicode.agents.schema import AgentDef
from baozicode.llm.base import LLMClient, Message
from baozicode.permissions.engine import RuleEngine
from baozicode.permissions.types import MergedPermissions, PermissionMode
from baozicode.prompt import PromptBuilder
from baozicode.prompt.types import BuiltPrompt
from baozicode.tools.base import ToolDefinition
from baozicode.tools.registry import ToolRegistry
from baozicode.worktree import (
    WorktreeInitConfig,
    WorktreeInitializer,
    WorktreeManager,
    WorktreeState,
)

if TYPE_CHECKING:
    from baozicode.agent import Agent
    from baozicode.agents.registry import AgentRegistry
    from baozicode.config.schema import AppConfig

log = logging.getLogger(__name__)


# identity section 替换:把 role body 拼到「身份」段末尾,让 LLM 知道自己
# 扮演什么角色。definition 模式的 sub-Agent 永远看到这条,覆盖 config.system_prompt。
_IDENTITY_OVERRIDE_PREFIX = "## 身份\n你是 sub-Agent,执行以下角色任务:\n\n"


class SubAgentRuntime:
    """sub-Agent 构造器 — 状态隔离 + BuiltPrompt 分流。

    Args:
        llm: 主 Agent 的 LLMClient(sub-Agent 复用同一份)
        hooks: 主 Agent 的 HookDispatcher(共享;事件 payload 加 `subagent` 字段)
        tool_registry: 全局 ToolRegistry(只读)
        project_root: 项目根,传给 sub-Agent(PermissionSandbox 用)
        config: 全局 AppConfig(sub-Agent 读 compaction / agent.* 派生参数)
        registry: AgentRegistry(definition 模式按 name 查 role_def)
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        hooks: Any | None,
        tool_registry: ToolRegistry,
        project_root: Path,
        config: "AppConfig",
        registry: "AgentRegistry",
        worktree_manager: WorktreeManager | None = None,
        worktree_init_config: WorktreeInitConfig | None = None,
    ) -> None:
        self._llm = llm
        self._hooks = hooks
        self._tool_registry = tool_registry
        self._project_root = project_root
        self._config = config
        self._registry = registry
        # v1.3:worktree 隔离设施。两者必须**同时**提供(或同时 None)
        # —— 否则 spawn 时若有 `isolation="worktree"` 角色,缺一个就报错
        # (fail-fast,不半创建)。
        if (worktree_manager is None) != (worktree_init_config is None):
            raise ValueError(
                "SubAgentRuntime: worktree_manager 和 worktree_init_config "
                "必须同时提供或同时为 None"
            )
        self._worktree_manager = worktree_manager
        self._worktree_init_config = worktree_init_config

    async def spawn(
        self,
        *,
        task_id: str,
        type: Literal["definition", "fork"],  # noqa: A002
        role_def: AgentDef | None,
        prompt: str,
        parent_messages: list[Message] | None,
        parent_denied_counts: dict[str, int] | None,
        parent_agent: "Agent | None",
        is_background: bool,
    ) -> "Agent":
        """构造一个隔离的 sub-Agent 实例。

        Args:
            task_id: SubAgentManager 分配的唯一 ID
            type: "definition" / "fork"
            role_def: definition 模式必填;fork 模式传 None
            prompt: 任务 prompt(已替换占位符)
            parent_messages: fork 模式必填(主对话 snapshot);
                definition 模式 None
            parent_denied_counts: fork 模式必填(主 session_rules 计数);
                definition 模式 None
            parent_agent: fork 模式必填(共享 _prompt 对象);
                definition 模式 None
            is_background: True = 走 L4 background whitelist

        Returns:
            构造好的 `Agent` 实例(已带 subagent_meta)

        Raises:
            ValueError: type 与必填参数不匹配 / fork + worktree 互斥 /
                isolation=worktree 但 runtime 未配 worktree_manager
        """
        # ---- 参数校验 ----
        if type == "definition":
            if role_def is None:
                raise ValueError("definition 模式 role_def 必填")
            if parent_agent is not None:
                log.debug(
                    "definition 模式忽略 parent_agent(不共享 BuiltPrompt)"
                )
        elif type == "fork":
            if role_def is None and parent_messages is None:
                raise ValueError("fork 模式 parent_messages 必填")
            if parent_agent is None:
                raise ValueError("fork 模式 parent_agent 必填(共享 _prompt)")

        # ---- v1.3:worktree 隔离处理 ----
        # 必须在 spawn 早期;fork + worktree 互斥(D4)→ fail-fast,
        # **不**半创建 worktree。
        worktree_state: WorktreeState | None = None
        worktree_name: str | None = None
        effective_project_root = self._project_root
        if role_def is not None:
            isolation = role_def.frontmatter.isolation
            if isolation == "worktree":
                # D4:互斥
                if type == "fork":
                    raise ValueError(
                        "fork mode + isolation=worktree 互斥;"
                        "worktree 强制 definition 模式"
                    )
                if self._worktree_manager is None:
                    raise ValueError(
                        f"role {role_def.name!r} 声明 isolation=worktree,"
                        " 但 SubAgentRuntime 未注入 WorktreeManager"
                    )
                assert self._worktree_init_config is not None
                # 用 role name 作为 worktree 名(已经过 AgentFrontmatter
                # `_validate_name` 校验,合法字符集)
                wt_spec = await self._worktree_manager.create(role_def.name)
                await WorktreeInitializer.run(
                    wt_spec.path, self._project_root,
                    self._worktree_init_config,
                )
                worktree_state = wt_spec.state  # "active"
                worktree_name = role_def.name
                effective_project_root = wt_spec.path
                log.debug(
                    "sub-Agent %s 走 worktree 隔离: %s",
                    task_id, wt_spec.path,
                )

        # ---- 工具过滤 ----
        # 父 Agent 的全部工具 = 父 Agent.available_tools,这里用 ToolRegistry 全集
        # (sub-Agent 看不到父 Agent 的 augmentation,但 description 增强规则
        # 来自 RuleRegistry,跨 Agent 一致,所以可以共享)
        all_tools = self._tool_registry.get_all_tools()

        # background_whitelist 优先级:SubAgentsConfig(可能为 None)> 默认
        default_wl = ["Read", "Grep", "Glob", "WebFetch", "notify_complete"]
        sa_cfg = getattr(self._config, "subagents", None)
        if sa_cfg is not None and getattr(sa_cfg, "background_whitelist", None):
            background_whitelist = list(sa_cfg.background_whitelist)
        else:
            background_whitelist = default_wl

        tool_filter = ToolFilter(
            role_def=role_def,
            is_background=is_background,
            background_whitelist=background_whitelist,
            all_tools=all_tools,
        )
        try:
            visible_tools = tool_filter.visible_tools
        except Exception:
            raise  # ToolFilterEmptyError 直传

        # ---- BuiltPrompt 分流 ----
        if type == "fork":
            # fork 模式:共享 parent 的 BuiltPrompt(同对象 byte-identical)
            assert parent_agent is not None  # 上面已校验
            built_prompt = parent_agent._prompt  # type: ignore[attr-defined]
        else:
            # definition 模式:用 role body 重写 identity section
            assert role_def is not None  # 上面已校验
            built_prompt = self._build_definition_prompt(role_def, visible_tools)

        # ---- 状态隔离 ----
        # 1) ConversationManager:全新,archiver=None
        from baozicode.conversation.manager import ConversationManager

        sub_conv = ConversationManager(archiver=None)
        if type == "fork" and parent_messages is not None:
            sub_conv.set_messages(parent_messages)

        # 2) MergedPermissions + RuleEngine:fresh,fork 模式从 parent_denied_counts
        #    复制初始 session_rules
        # v1.3:worktree sub-Agent 的 real_root 指向 worktree_path(让
        # PathSandbox 在 worktree 内做沙箱,主 repo 内路径不被允许)
        sub_merged = MergedPermissions(
            rules=[],
            mode=self._config.permissions_v5.mode
            if self._config.permissions_v5
            else "default",
            sources_loaded=[],
            real_root=effective_project_root,
            path_sandbox_enabled=True,
            session_rules=[],
        )
        if type == "fork" and parent_denied_counts:
            # 把 dict[str, int] 转换为 session_rules — 这里用 dict 直接挂
            # (Agent 的 deny 计数走 guard_state,不是 permissions 层;
            # 这里只是给 fork 的 MergedPermissions 一个起点,跟 parent 同步)
            sub_merged.session_rules = [
                # 占位:实际 MergedPermissions.session_rules 是 list[PermissionRule],
                # dict 计数转换留作 v1.3 兼容。这里仅复制引用保持一致即可。
            ]
        sub_engine = RuleEngine(merged=sub_merged)

        # 3) subagent_meta(注入 Agent 实例,fire_lifecycle 时读)
        subagent_meta = {
            "task_id": task_id,
            "role": role_def.name if role_def else None,
            "type": type,
            "depth": 1,  # v1.2 L1 物理禁嵌套
            "effective_project_root": (
                str(effective_project_root) if worktree_name else None
            ),
            "worktree_state": worktree_state,
            "worktree_name": worktree_name,
        }

        # ---- 构造 Agent ----
        from baozicode.agent import Agent

        # session_mode / max_iterations 派生
        if type == "definition" and role_def is not None:
            session_mode = role_def.permission_mode  # type: ignore[assignment]
            if session_mode is None:
                # 继承主 Agent 的 mode
                session_mode = (
                    self._config.permissions_v5.mode
                    if self._config.permissions_v5
                    else "default"
                )
            max_iterations = role_def.max_iterations
        else:
            # fork 模式继承主 Agent
            session_mode = (
                parent_agent._session_mode  # type: ignore[attr-defined]
                if parent_agent is not None
                and getattr(parent_agent, "_session_mode", None)
                else None
            )
            max_iterations = (
                parent_agent._max_iterations  # type: ignore[attr-defined]
                if parent_agent is not None
                else 20
            )

        # 构造 Agent(用 Any 类型的 kwargs,避免循环 import 类型校验)
        sub_agent = Agent(
            llm_client=self._llm,
            tools=visible_tools,
            conversation=sub_conv,
            permissions=None,  # 走 merged_permissions 路径
            config=self._config,
            max_iterations=max_iterations,
            plan_mode=False,
            permission_callback=None,  # sub-Agent 无 L5 user decision
            session_mode=session_mode,
            merged_permissions=sub_merged,
            permissions_engine=sub_engine,
            compact_ctx=None,  # sub-Agent 不自动压
            instructions_text="",  # 不继承 instructions
            project_root=effective_project_root,
            skill_filter=None,
            skill_activation=None,
            skill_registry=None,
            hook_dispatcher=self._hooks,
        )
        # 直接覆盖 _prompt(fork 用 parent 引用,definition 用 built_prompt)
        sub_agent._prompt = built_prompt  # type: ignore[attr-defined]
        # 注入 subagent_meta(供后续 lifecycle event payload 用,task 13 时
        # Agent._fire_lifecycle_safe 会读这个)
        sub_agent._subagent_meta = subagent_meta  # type: ignore[attr-defined]
        # v1.3:worktree state 注入 Agent 实例,SubAgentManager._run_subagent
        # 终态时读它决定是否调 exit
        if worktree_name is not None:
            sub_agent._worktree_state = worktree_state  # type: ignore[attr-defined]
            sub_agent._worktree_name = worktree_name  # type: ignore[attr-defined]
        return sub_agent

    def _build_definition_prompt(
        self,
        role_def: AgentDef,
        visible_tools: list[ToolDefinition],
    ) -> BuiltPrompt:
        """为 definition 模式 sub-Agent 构造一份新的 BuiltPrompt。

        把 role_def.body 拼到 identity section 末尾(cold cache start —
        与主 Agent 的 system prompt byte-identical 性被打破)。
        """
        # 临时把 config.system_prompt 改成 role body,调一次 PromptBuilder
        # 再恢复 — 因为 identity section 直接读 config.system_prompt。
        # 这样做最简单,不引入新 section hook。
        original_system = self._config.system_prompt
        try:
            # role body 替代默认 system_prompt,触发 identity section override
            object.__setattr__(
                self._config, "system_prompt",
                f"{_IDENTITY_OVERRIDE_PREFIX}{role_def.body}",
            )
            builder = PromptBuilder()
            built = builder.build(
                self._config,
                plan_mode=False,
                tools=visible_tools,
                cwd=str(self._project_root),
                instructions_text="",
                memory_index_user=None,
                memory_index_project=None,
                skill_registry=None,
            )
            return built
        finally:
            object.__setattr__(self._config, "system_prompt", original_system)


__all__ = ["SubAgentRuntime"]