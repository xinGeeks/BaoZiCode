"""Hooks 系统 — Pydantic schema 定义。

四层 + 系统事件总共 11 个 EventName；
HookDefYaml 是 `config.yaml: hooks:` 块的根节点；
ConditionYaml 在 if. 之下,支持 all / any 二选一；
MatcherYaml 是单条 match 规则(精确 / glob / regex / not_ 三种变体)；
ActionYaml 是 6 种动作的 tagged union(shell / http / prompt / sub-agent /
clear_sticky_reminders / clear_stable_system_overrides)。

校验规则(在 HookRegistry.freeze() 阶段执行,Pydantic 不全挡住):
- id 唯一
- actions 非空
- event ∈ 11 允许列表
- shell / prompt action 拒绝 deny / deny_reason / parse_expr
- http / sub-agent 配 deny 时必须配 parse_expr(运行期评估用)+ deny_reason
- tool.pre 不允许 async
- if.all 和 if.any 不同时存在
- slot=stable_system 不允许在 tool.pre/post 用
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


EventName = Literal[
    "session.start",
    "session.end",
    "turn.start",
    "turn.end",
    "message.received",
    "message.sent",
    "tool.pre",
    "tool.post",
    "system.error",
    "system.compaction",
    "system.cancel",
]

ALL_EVENTS: tuple[str, ...] = (
    "session.start", "session.end",
    "turn.start", "turn.end",
    "message.received", "message.sent",
    "tool.pre", "tool.post",
    "system.error", "system.compaction", "system.cancel",
)

SYNC_ONLY_EVENTS: frozenset[str] = frozenset({"tool.pre"})

STABLE_SYSTEM_FORBIDDEN_EVENTS: frozenset[str] = frozenset({"tool.pre", "tool.post"})


class MatchValue(BaseModel):
    """单条 matcher 的实际值。kind 决定怎么比对,value 是字符串模式。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "exact", "glob", "regex",
        "not_exact", "not_glob", "not_regex",
    ]
    value: str


class MatcherYaml(BaseModel):
    """if.all / if.any 之下的单条 match 规则。

    两种形态互斥:
    - `tool: str` 精确匹配工具名
    - `arg.<name>: MatchValue` 匹配工具的某个 argument

    Pydantic 不直接支持动态 key,所以下面两种都允许但不互斥。
    互斥校验在 HookRegistry.freeze() 阶段做。
    """

    model_config = ConfigDict(extra="forbid")

    tool: str | None = None
    arg: dict[str, MatchValue] = Field(default_factory=dict)


class ConditionYaml(BaseModel):
    """if. 之下的复合条件。all / any 二选一,同时存在 → freeze 阶段报错。"""

    model_config = ConfigDict(extra="forbid")

    all: list[MatcherYaml] | None = None
    any: list[MatcherYaml] | None = None


class _ShellAction(BaseModel):
    """shell action:跑 bash -c <command>。exit_code 决定 deny。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["shell"]
    command: str
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    enqueue: bool = False


class _HttpAction(BaseModel):
    """http action:发 HTTP 请求。4xx/5xx 不自动拒;要 deny 需配 parse_expr。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["http"]
    url: str
    method: Literal["GET", "POST"] = "GET"
    body: dict[str, Any] | None = None
    parse_expr: str | None = None
    deny_reason: str | None = None

    @field_validator("url")
    @classmethod
    def _url_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("http action: url 必须非空")
        return v


class _PromptAction(BaseModel):
    """prompt action:注入文字到 system prompt。slot 决定注入位置。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["prompt"]
    content: str
    slot: Literal["sticky_reminder", "stable_system", "temp"] = "sticky_reminder"
    enqueue: bool = True

    @field_validator("content")
    @classmethod
    def _content_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("prompt action: content 必须非空")
        return v


class _SubAgentAction(BaseModel):
    """sub-agent action:启动子 Agent(v1.1 占位,v1.1.1 实现实际执行)。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["sub-agent"]
    goal: str
    parse_expr: str | None = None
    deny_reason: str | None = None

    @field_validator("goal")
    @classmethod
    def _goal_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sub-agent action: goal 必须非空")
        return v


class _ClearStickyAction(BaseModel):
    """v1.2 control action:清空 Agent._pending_reminders(sticky `hook_prompt` 队列)。

    无其他字段 — `deny` / `deny_reason` / `parse_expr` 均不允许(control action,
    extra=forbid 由 Pydantic 自动拦截)。语义独立:**不动** `_hook_stable_overrides`
    或 `_temp_reminders`。
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["clear_sticky_reminders"]


class _ClearStableAction(BaseModel):
    """v1.2 control action:清空 Agent._hook_stable_overrides(钉在 stable_system 末尾的段)。

    语义独立:**不动** `_pending_reminders` 或 `_temp_reminders`。
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["clear_stable_system_overrides"]


ActionYaml = Annotated[
    Union[
        _ShellAction,
        _HttpAction,
        _PromptAction,
        _SubAgentAction,
        _ClearStickyAction,
        _ClearStableAction,
    ],
    Field(discriminator="action"),
]


class HookDefYaml(BaseModel):
    """`config.yaml: hooks:` 数组的单条规则定义。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    event: EventName
    if_: ConditionYaml | None = Field(default=None, alias="if")
    actions: list[ActionYaml] = Field(min_length=1)
    async_: bool = Field(default=False, alias="async")
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    run_once: bool = False

    @field_validator("id")
    @classmethod
    def _id_no_whitespace(cls, v: str) -> str:
        if any(c.isspace() for c in v):
            raise ValueError("hook id 不能包含空白字符")
        return v


def parse_hook_def(raw: dict[str, Any]) -> HookDefYaml:
    """把 YAML 里读出的 dict 解析成 HookDefYaml。

    Pydantic ValidationError 转 HookValidationError,携带字段路径。
    """
    # 延迟 import 避免循环
    from baozicode.hooks._errors import HookValidationError
    try:
        return HookDefYaml.model_validate(raw)
    except ValidationError as exc:
        raise HookValidationError.from_pydantic(exc) from exc
