"""工具调用统一数据模型。

`ToolDefinition / ToolCall / ToolResult` 三个 dataclass 是后端、工具实现、TUI 共用的内部契约。
后端 SDK 类型不许泄漏出 `baozicode/llm/`。
"""
from __future__ import annotations

import locale
import logging
from dataclasses import dataclass, field
from typing import Any, Literal


Risk = Literal["low", "high"]
ExecutionStatus = Literal[
    "block_l1",
    "block_hook_pre",
    "block_permission",
    "executed_success",
    "executed_failed",
]
DeniedBy = Literal["l1_blacklist", "hook_pre", "l2_l5_permission"]

# v1.4:角色白名单 — Agent 在构造时指定 role,ToolRegistry 过滤可见工具。
# 默认 None 表示全员可见;team_* 协作工具由 Lead-only 限定。
AGENT_ROLES = frozenset({"lead", "member", "subagent", "coordinator"})


log = logging.getLogger(__name__)


def decode_subprocess_output(data: bytes) -> str:
    """智能解码子进程输出。

    Windows 中文系统上 cmd/PowerShell/部分 rg 编译版默认 GBK,
    直接 UTF-8 解会乱码。本函数先 UTF-8 严格解码,失败回落到
    系统偏好编码,再不行用 cp936 兜底,最后 errors="replace"。
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode(locale.getpreferredencoding(False))
    except (UnicodeDecodeError, LookupError):
        pass
    try:
        return data.decode("cp936")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


@dataclass
class ToolDefinition:
    """工具的静态描述，喂给 LLM 让它知道这个工具能干嘛。

    `side_effect` (v0.3 新增):声明此工具调用是否有外部副作用。
    - True:Write / Edit / Bash —— 改文件或执行 shell 命令
    - False:Read / Grep / Glob / WebFetch —— 只读,可并发
    调度器用此字段决定并发 vs 串行;Plan Mode 用此字段做工具过滤。
    默认 False 保证 v0.2 调用方零修改。

    `path_args` (v0.5 新增):声明此工具的哪些 argument 取值是文件系统路径。
    L2 路径沙箱用此字段做 sandbox check;Bash 的 path 提取走独立 regex。
    例:Read/Write/Edit → ["file_path"];Grep/Glob → ["path"];Bash → []。

    `tool_type` (v1.0 新增):区分普通工具 / 内部工具。
    - None 或 "user":常规工具,经过白名单防御
    - "internal":系统级工具(如 `load_skill`),不受白名单约束(boot 时确保仅
      注册到 LLM 的工具集,然后永远可用)
    v0.6 之前没有此字段,所有工具默认为 None/"user"。

    `role_visibility` (v1.4 新增):声明此工具对哪些 Agent 角色可见。
    - None(默认) — 所有角色可见(向后兼容,7 个内置工具全部 None)
    - ['lead'] — 仅 Lead Agent 可见(如 team_dispatch / team_merge)
    - ['lead', 'coordinator'] — Lead 和未来 coordinator 都可见
    ToolRegistry.get_all_tools(role) 按 role 过滤;role=None 时返全部
    (老路径,v1.3 行为)。
    """

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    risk: Risk = "low"
    side_effect: bool = False
    path_args: list[str] = field(default_factory=list)
    tool_type: str | None = None  # None = "user",显式 "internal" = 系统级豁免
    role_visibility: list[str] | None = None  # None = 全员可见;list = 受限角色

    def __post_init__(self) -> None:
        # role_visibility 校验:None 透传;list 必须是已知角色子集
        if self.role_visibility is not None:
            if not isinstance(self.role_visibility, list):
                raise ValueError(
                    f"ToolDefinition.role_visibility 必须是 list 或 None,"
                    f" 得到 {type(self.role_visibility).__name__}"
                )
            invalid = [r for r in self.role_visibility if r not in AGENT_ROLES]
            if invalid:
                raise ValueError(
                    f"ToolDefinition.role_visibility 含未知角色 {invalid!r};"
                    f" permitted: {sorted(AGENT_ROLES)}"
                )

    def to_anthropic(self) -> dict[str, Any]:
        """转换为 Anthropic SDK 的 tool 格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_openai(self) -> dict[str, Any]:
        """转换为 OpenAI 兼容的 function calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolCall:
    """LLM 在流中发起的工具调用请求。"""

    id: str
    name: str
    arguments: dict[str, Any]
    error: str | None = None  # 流式累积时 JSON 解析失败的诊断信息


@dataclass
class ToolResult:
    """工具执行结果,喂回给 LLM。

    v1.1 新增字段:
    - `execution_status`:v1.1 pipeline(L1→hook.pre→L2-L5→execute→hook.post)各阶段状态。
      None 表示"v1.0 风格 / 旁路构造",is_error 走调用方显式值;非 None 时 is_error 由
      `__post_init__` 派生为 `(execution_status != "executed_success")`。
    - `denied_by`:被哪一层挡的。仅 `execution_status ∈ block_*` 时填;否则 None。
    - `denied_hook_id`:首个 deny 的 hook id;仅 `execution_status == block_hook_pre` 时填。

    老调用方式(`ToolResult(tool_call_id, content, is_error=True)`)完全兼容。
    """

    tool_call_id: str
    content: str
    is_error: bool = False
    # v1.1 hook-aware pipeline 字段
    execution_status: ExecutionStatus | None = None
    denied_by: DeniedBy | None = None
    denied_hook_id: str | None = None
    # v0.7 offload 字段
    offloaded_to: str | None = None
    original_size: int = 0

    def __post_init__(self) -> None:
        # v1.1:execution_status 设置时派生 is_error(覆盖显式传入值)
        if self.execution_status is not None:
            self.is_error = self.execution_status != "executed_success"
            # 防呆:execution_status ∈ block_* 但 denied_by 是 None
            if self.execution_status.startswith("block_") and self.denied_by is None:
                log.warning(
                    "ToolResult 构造异常:execution_status=%s 但 denied_by=None",
                    self.execution_status,
                )
            # 防呆:execution_status == block_hook_pre 但 denied_hook_id 空
            if (
                self.execution_status == "block_hook_pre"
                and not self.denied_hook_id
            ):
                log.warning(
                    "ToolResult 构造异常:block_hook_pre 但 denied_hook_id 空",
                )

    @classmethod
    def error_result(cls, tool_call_id: str, message: str) -> "ToolResult":
        return cls(tool_call_id=tool_call_id, content=message, is_error=True)

    @classmethod
    def success(cls, tool_call_id: str, content: str) -> "ToolResult":
        return cls(tool_call_id=tool_call_id, content=content, is_error=False)


__all__ = [
    "AGENT_ROLES",
    "DeniedBy",
    "ExecutionStatus",
    "Risk",
    "ToolDefinition",
    "ToolCall",
    "ToolResult",
]
