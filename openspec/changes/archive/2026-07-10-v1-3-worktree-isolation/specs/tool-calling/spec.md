# tool-calling Specification — v1.3 ADDED Requirements

> This is the v1.3 delta on top of existing tool-calling capability.
> v0.3 Bash 工具契约 + 7 工具列表 **不变**。v1.3 给 Bash 工具加
> optional `cwd` 参数(forward-looking,为 sub-Agent 在 worktree 下用
> 显式 cwd 跑命令)。

## ADDED Requirements

### Requirement: Bash tool optional `cwd` parameter (NEW)

In v1.3, `Bash.execute(arguments)` MUST 接受 optional
`cwd: str | None = None` 参数:

- 缺省(`cwd=None`)→ **现有 v1.2 行为完全不变**(`BashSession.
  plan_cd` + 更新 `_sessions.cwd`)
- 非缺省 → `cwd` 作为 `asyncio.create_subprocess_shell` 的
  `cwd=...` 参数;**不**调 `plan_cd`,**不**更
  `_sessions.cwd`(fire-and-forget)

**安全校验**:

- `cwd` 必须是绝对路径
- `cwd` 必须 resolve 后在某个有效 root 内(main project_root
  或 sub-Agent 的 worktree path);通过 closure `_cwd_validator`
  判定(`SubAgentManager` 在 spawn 时注入)

#### Scenario: Default cwd unchanged (zero compat break)
- **WHEN** Bash 调 `execute({"command": "ls"})`(没有 cwd)
- **THEN** 走现有 `BashSession.plan_cd` + `_sessions.cwd` 跟
  踪,v1.2 行为**完全**不变

#### Scenario: Explicit cwd from worktree sub-Agent
- **WHEN** worktree sub-Agent 调
  `execute({"command": "ls src/", "cwd": "<worktree_path>"})`
- **THEN** `subprocess` 用 `cwd=<worktree_path>` 跑;`_sessions
  .cwd` **不**变;执行结果正常返回

#### Scenario: cwd relative rejected
- **WHEN** Bash 调 `execute({"command": "ls", "cwd": "./src"})`
- **THEN** 拒绝,`ToolResult.error_result("", "Bash: cwd 必须
  是绝对路径,得到 './src'")`

#### Scenario: cwd outside any root rejected
- **WHEN** Bash 调
  `execute({"command": "ls", "cwd": "/etc/passwd"})`
- **THEN** 拒绝,`ToolResult.error_result("", "Bash: cwd
  '/etc/passwd' 不在任何有效 root 内")`;closure
  `_cwd_validator` 判定 escape

#### Scenario: cwd set_cwd_validator closure injection
- **WHEN** `SubAgentManager._run_subagent` 跑前调
  `baozicode.tools.bash.set_cwd_validator(...)` 注入 closure,
  跑完调 `set_cwd_validator(None)` 清
- **THEN** Bash.execute 在 `cwd` 校验时调用最新 closure;主
  Agent 不传就 `_cwd_validator is None` → 校验退回到 main
  project_root(`session._is_inside`)

#### Scenario: Bash ToolDefinition schema updated
- **WHEN** LLM 收到 `task` 工具 + Bash 工具描述
- **THEN** Bash 工具 schema 含
  `cwd: string (optional)` 字段 + description 说明 "绝对路径,
  留空走默认 session cwd"

## MODIFIED Requirements

无(没有改 Bash 工具 v0.3 的核心 `command` + `timeout` 字段)。

## REMOVED Requirements

无。
