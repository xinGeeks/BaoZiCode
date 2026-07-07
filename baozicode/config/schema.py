"""配置数据模型。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BackendName = Literal["anthropic", "openai", "minimax", "deepseek"]


class BackendConfig(BaseModel):
    """单个 LLM 后端的配置。"""

    api_key: str
    model: str
    base_url: str | None = None


class Permissions(BaseModel):
    """工具调用权限控制(v0.2)。

    - `auto_allow`: 跳过高风险确认 Modal 的工具名列表(仍执行,仍记录卡片)
    - `deny`: 永远拒绝执行的工具名/glob 列表(命中直接 is_error,弹红 ✗ 卡片)
    - `batch_confirm`: 高风险工具连续同类型 ≥2 次时,第一次 Modal 提供
      "Allow all remaining" 按钮
    - `bash_locked_cwd`: 是否把 Bash 会话 cwd 锁回项目根(关掉 cd 跟随)

    任何 v0.2 之后新增的字段都会被 silent ignore(`extra="ignore"`)。
    """

    model_config = ConfigDict(extra="ignore")

    auto_allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    batch_confirm: bool = False
    bash_locked_cwd: bool = False


_DEFAULT_PERMISSIONS = Permissions()


class RulesConfig(BaseModel):
    """v0.4 — 7 条关键规则的开关。

    每条规则都同时出现在 system prompt 的「工具使用关键规则」section
    和相关工具的 description 开头(`RuleRegistry.augment_tool`)。
    把任一字段设为 False,规则既不会出现在 prompt 里,也不会注入工具描述。
    """

    model_config = ConfigDict(extra="ignore")

    edit_requires_read: bool = True
    prefer_specialized_tools: bool = True
    bash_timeout: bool = True
    parallel_limit: bool = True
    error_then_decide: bool = True
    absolute_paths: bool = True
    webfetch_to_file: bool = True


class AgentConfig(BaseModel):
    """v0.3 引入 — Agent 主循环参数 + v0.4 规则与提醒节奏。

    `max_iterations` 是安全网,Agent 跑到这个迭代次数还没结束就强制终止
    (`done.reason=MAX_ITERATIONS_REACHED`)。默认 20 足以应对绝大多数任务,
    又能在 LLM 进入死循环时及时阻断。

    v0.5 新增:
    - `denial_warn_threshold`:连续拒绝次数达到此值,向 LLM 注入 reminder
      提醒它调整策略(不终止 loop)
    """

    model_config = ConfigDict(extra="ignore")

    max_iterations: int = 20
    enable_system_reminders: bool = True
    plan_reminder_interval: int = 5
    denial_warn_threshold: int = 5
    rules: RulesConfig = Field(default_factory=RulesConfig)


class PermissionRuleYaml(BaseModel):
    """v0.5 — 配置文件里单条权限规则的 Pydantic 表示。

    与 `baozicode.permissions.types.PermissionRule`(dataclass)对应,
    但 `source` 字段在加载时由 loader 注入,不在 YAML 里手写。
    """

    model_config = ConfigDict(extra="ignore")

    tool: str
    pattern: str
    decision: Literal["allow", "deny"]


class PermissionsV5(BaseModel):
    """v0.5 — 新版权限系统,直接嵌在 `config.yaml` 顶层。

    - `mode` 全局默认模式(可选;local YAML 仍可覆盖)
    - `rules` 静态规则列表(项目级 / 用户级均可放)
    - 运行时动态的 session rule / local YAML 不进 Pydantic schema,
      走 `MergedPermissions` / `persistence.append_rule_to_local_yaml`
    """

    model_config = ConfigDict(extra="ignore")

    mode: Literal["strict", "default", "permissive"] = "default"
    rules: list[PermissionRuleYaml] = Field(default_factory=list)


class AppConfig(BaseModel):
    """BaoZiCode 全局配置。

    四个后端的 `BackendConfig` 块全部必填——这样切换后端时只需改一个字段
    （`backend:`），不需要再去配另一块；Pydantic 校验也会立刻指出缺什么。

    v0.4 新增字段:
    - `custom_instructions` 用户追加的额外说明(喂给 PromptBuilder 的 custom section)
    - `skills_dir` skills 文件目录(扫不到时静默跳过)
    - `memory_path` 长效记忆文件(不存在时静默跳过)

    v0.5 新增字段:
    - `permissions_v5` 新版三层 YAML 规则(可选;为空时走 v0.2 旧 Permissions)
    """

    backend: BackendName
    system_prompt: str = "You are BaoZiCode, a helpful AI coding assistant."
    custom_instructions: str = ""
    skills_dir: Path = Field(default_factory=lambda: Path("~/.config/baozicode/skills").expanduser())
    memory_path: Path = Field(default_factory=lambda: Path("~/.config/baozicode/memory.md").expanduser())
    anthropic: BackendConfig
    openai: BackendConfig
    minimax: BackendConfig
    deepseek: BackendConfig
    permissions: Permissions | None = None
    permissions_v5: PermissionsV5 | None = None
    agent: AgentConfig | None = None

    def active(self) -> BackendConfig:
        """返回当前激活后端的配置。"""
        return getattr(self, self.backend)

    def all_backends(self) -> list[tuple[BackendName, BackendConfig]]:
        """返回全部 4 个后端,按固定顺序。"""
        return [
            ("anthropic", self.anthropic),
            ("openai", self.openai),
            ("minimax", self.minimax),
            ("deepseek", self.deepseek),
        ]

    def active_permissions(self) -> Permissions:
        """返回当前生效的 permissions,`None` 时回退到全默认。"""
        return self.permissions if self.permissions is not None else _DEFAULT_PERMISSIONS

    def active_agent(self) -> AgentConfig:
        """返回当前生效的 agent 配置,`None` 时回退到全默认(max_iterations=20)。"""
        return self.agent if self.agent is not None else AgentConfig()


__all__ = [
    "AgentConfig",
    "AppConfig",
    "BackendConfig",
    "BackendName",
    "PermissionRuleYaml",
    "Permissions",
    "PermissionsV5",
    "RulesConfig",
]
