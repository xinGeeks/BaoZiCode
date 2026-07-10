# Design — v1.3 Worktree Isolation

## Context

v1.2 SubAgent 系统让主 Agent 动态派 sub-Agent,但所有 sub-Agent 共享同
一个 `project_root`(PathSandbox.real_root + BashSession.cwd + 文件系
统视角一致)。这有两个根本问题:

- **并发不安全** — 两个并行 sub-Agent 同时 `Edit(src/foo.py)` 会覆盖
  对方改动;LLM 不知道主 Agent 跟自己在同时改同一份文件
- **隔离语义模糊** — user-facing 概念("sub-Agent 跑在自己的环境")跟
  实现(同一个 cwd)不一致

v1.3 引入 **`isolation: worktree`** 让 sub-Agent 用 **git worktree** 工
作。git worktree 是 git 自带的多 working directory 机制:同一 repo 挂
多目录、共享对象库、各自在独立分支。完美匹配"sub-Agent 隔离"语义。

完整设计见本文件各 Decision 段。

## Goals / Non-Goals

**Goals:**

- sub-Agent 声明 `isolation: worktree` → 自动建 worktree 在
  `<repo>/.worktrees/<safe-name>/`
- 主 Agent 进程 **不 chdir**,worktree 路径通过新 `cwd` 参数显式传
- worktree sub-Agent 的所有文件操作(Read/Write/Edit/Grep/Glob/Bash)
  物理落在 worktree 内
- LLM `BuiltPrompt` 因 `env_info.cwd` 段不同而自然分裂,**无新缓存层**
- 环境初始化软链 / 复制按配置白名单
- 退出有未提交 / 未推送 → 默认保留(`detached`);无变更 → 干净清理
- 后台清理按三层过滤自动跑,默认 60s 一扫
- 路径名严格安全校验(防 LLM 输入路径遍历)
- Bash 工具加 `cwd` optional 参数(forward-looking;不破坏 v1.2 行为)

**Non-Goals:**

- Worktree 之间合并策略(交给上层用 `git merge` 决定)
- 跨 worktree 代码同步(从主 repo `cp` 到 worktree 是 v1.4 的事)
- 多 Agent 并行编排(谁先谁后由主 Agent 决策 LLM)
- worktree 内嵌套 worktree(L1 `task` 工具全局 deny 物理禁止)
- fork mode + worktree(本决策见 D4)
- watch `.worktrees/` 做 sub-Agent 热发现
- 工作树内多 Python venv 的管理(只管 link 软链)
- 重写 Bash 工具契约(`cwd` 是 optional,默认行为 0 变化)

## Decisions

### D1: 路径名校验规则

**决策**:`^[a-zA-Z0-9_./-]+$`,长度 1–64,拒绝 `.` 和 `..` 段,允许
`/` 做嵌套命名。

**实现**:`baozicode/worktree/schema.py:WorktreePathValidator.validate(name)`
纯函数,无 IO,纯字符串过滤。失败抛 `PathValidationError`。

**依据**:
- 用户拍板"字符集宽松 + 严格嵌套"
- 防 LLM 输入触发:路径遍历(`../../../etc/passwd`)、symlink 攻击、shell
  expansion —— 全在字符集层面被拒
- `/` 是 sub-agent 名内部的嵌套语义(如 `phase1/api-designer`,
  `experiments/abc-123`),**不是文件系统层级跳转**;v1.3 拒绝 `..` 段
  保证嵌套内部安全

```python
_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_./-]+$")
_MAX_LEN = 64

def validate(name: str) -> None:
    if not name or len(name) > _MAX_LEN:
        raise PathValidationError(f"name 长度越界 (1 ≤ n ≤ {_MAX_LEN})")
    if name.startswith("/") or name.endswith("/"):
        raise PathValidationError("不允许开头或结尾的 /")
    parts = name.split("/")
    if any(p in {"", ".", ".."} for p in parts):
        raise PathValidationError(f"拒绝 . 或 .. 段: {name!r}")
    if not _VALID_NAME_RE.match(name):
        raise PathValidationError(f"字符集超出 [a-zA-Z0-9_./-]: {name!r}")
```

### D2: worktree 目录位置

**决策**:`<repo>/.worktrees/<safe-name>/`,自动加进 `<repo>/.gitignore`。

**依据**:
- 用户拍板
- 项目内层级,跟 `.baozicode/` 同级,跟 `.git/` 不冲突
- `git worktree` 不在意目录名,只要路径不在 repo 内现有 tracked 路径
  上
- `.gitignore` 隐藏让 `git status` 永远不会看到 worktree 内的临时文件

**实现**:`WorktreeManager.create` 第 1 步跑 `git worktree add -b
wt/<safe-name> <repo>/.worktrees/<safe-name>/ <start-point>`;
`WorktreeInitializer` 第 4 步 idempotent 插 `.worktrees/` 行。

### D3: frontmatter `isolation` 字段默认不开

**决策**:`AgentFrontmatter.isolation: Literal["worktree"] | None = None`,
**默认 None = 不开**;需要 worktree 隔离的 AGENT.md 显式写
`isolation: worktree`。

**依据**:
- 用户拍板"全部默认关,显式声明才开"
- builtin `explorer` / `summarizer` frontmatter **不变**(只读 / 纯压缩
  场景,worktree 价值低)
- 用户 / project 级自定义 agent 需要时显式声明;Pydantic `Literal` 强
  校验拼错报错

**实现**:

```python
# baozicode/agents/schema.py
class AgentFrontmatter(BaseModel):
    ...
    isolation: Literal["worktree"] | None = None
    # 跟 nesting-depth 平级;Pydantic 自动校验
```

### D4: fork + worktree 互斥

**决策**:`SubAgentRuntime.spawn` 检测
`type == "fork" and role_def.frontmatter.isolation == "worktree"` → 抛
`ValueError("fork mode + isolation=worktree 互斥;worktree 强制
definition 模式")`。

**依据**:
- 用户拍板
- fork mode 共享 `parent_agent._prompt` 走 Anthropic cache;BuiltPrompt
  含 `env_info.cwd` 段(看 `baozicode/prompt/sections/env_info.py`),worktree
  sub-Agent cwd 不同 → byte-identical 性质被打破 → cache miss
- fork 概念上是"延续父的视角思考" + "换工作目录",逻辑上冲突
- error 路径(LLM 把这俩字段拼一起时)fail-fast,不是 silent behavior
  变化

**实现**:`SubAgentRuntime.spawn` 在调用 `_isolation_required = ...` 之
前先做这个断言:

```python
if type == "fork" and role_def.frontmatter.isolation == "worktree":
    raise ValueError(
        "fork mode + isolation=worktree 互斥;worktree 强制 definition 模式"
    )
```

### D5: Bash 工具加 `cwd` optional 参数

**决策**:`Bash.execute(arguments)` 接 `cwd: str | None = None`:
- None → 现有 `BashSession.plan_cd` + `_sessions.cwd` 维护,**完全等价
  v1.2**(零兼容性问题)
- 非 None → `cwd` 作为 `subprocess` 的 `cwd=...` 参数;执行完不更
  `_sessions.cwd`(把临时 cwd 处理成 fire-and-forget)

**依据**:
- 用户拍板"改 Bash 工具加 cwd 参数"
- 现有 Bash 工具合约是 v0.3 老约定,改工具签名会有 blast radius;**加
  optional 参数** 是最小破坏
- 主 Agent 行为零变化(LLM 不主动传 cwd → 走老路径);worktree sub-Agent
  每个 Bash 调用前由 dispatch 路径(或工具 schema augmentation)在入参
  里塞 `cwd=<worktree_path>`

**实现**:

```python
async def execute(arguments: dict) -> ToolResult:
    command = arguments.get("command")
    if not command:
        return ToolResult.error_result("", "...")

    cwd_override = arguments.get("cwd")

    project_root = _resolve_default_root()
    if project_root is None:
        return ToolResult.error_result("", "...")

    session = get_session(project_root)
    if session is None:
        session = configure(project_root)

    timeout_raw = arguments.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    timeout = max(1, min(timeout, MAX_TIMEOUT_SECONDS))

    # ---- 新逻辑:显式 cwd ----
    if cwd_override is not None:
        # 安全校验 1:必须绝对路径
        override_path = Path(cwd_override)
        if not override_path.is_absolute():
            return ToolResult.error_result(
                "", f"Bash: cwd 必须是绝对路径,得到 {cwd_override!r}"
            )
        # 安全校验 2:必须在某个有效 root 内(主或 worktree)
        # 由 permission 检查路径(本函数仍 plan_cd 验证 cd,但 cwd
        # override 是高层传入,不调 plan_cd)
        # Bash 的 cwd 由 SubAgentManager 在 spawn 时传,
        # 真实工作流下已经是 worktree 内
        if not override_path.exists():
            return ToolResult.error_result(
                "", f"Bash: cwd 不存在: {cwd_override}"
            )
        if not override_path.is_dir():
            return ToolResult.error_result(
                "", f"Bash: cwd 不是目录: {cwd_override}"
            )
        # 强制 resolve 防 symlink 逃逸
        resolved = override_path.resolve()
        if not session._is_inside(resolved) and not _is_inside_worktree(resolved):
            # _is_inside_worktree 校验在 SubAgentManager._run_subagent
            # 已 bind 的 effective_project_root 内
            return ToolResult.error_result(
                "", f"Bash: cwd {cwd_override} 不在任何有效 root 内"
            )

        subprocess_cwd = str(resolved)
        # 不调 plan_cd,不更 _sessions.cwd
    else:
        new_cwd, plan_err = session.plan_cd(command)
        if plan_err is not None:
            return ToolResult.error_result(...)
        subprocess_cwd = str(new_cwd)
        # 现有 v1.2 行为:执行完 commit session.cwd
        session.cwd = new_cwd

    # ---- 后续:run subprocess ----
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=subprocess_cwd,
        )
    except OSError as exc:
        return ToolResult.error_result("", f"Bash: failed to spawn: {exc}")
    ...
```

(`_is_inside_worktree` 实际是 SubAgentManager 注入的 closure,见 D11)

### D6: 环境初始化走配置白名单

**决策**:`worktree:WorktreeConfig` 块有 `link_paths: list[str]` 和
`copy_paths: list[str]` 两个白名单,**LLM 不能 spawn 时 override**。

**依据**:
- 用户拍板"A. 配置白名单"
- 不引入"自动探测 .gitignore"(智能但隐式)
- 不引入"用户 hook 脚本"(门槛高)
- 明确、可审、默认安全

**默认**:

```python
@dataclass
class WorktreeConfig:
    enabled: bool = True
    link_paths: list[str] = [
        ".venv",
        "node_modules",
        ".cargo",
        # 软链典型大目录(几百 MB 起,值得链)
    ]
    copy_paths: list[str] = [
        ".baozicode/BaoZiCode.md",
        ".env",
        "config.yaml",
        ".claude/",
        # 复制运行时必需但体积小的配置文件
    ]
    retention_minutes: int = 60
    daemon_interval_seconds: int = 60
    max_concurrent_worktrees: int = 5
```

**实现**:`WorktreeInitializer.run(setup_dir, config)` 4 步:
1. **link**:对 `link_paths` 每项 `os.symlink(setup_dir/p, worktree/p)`
   (相对路径);Windows fallback `subprocess.run("mklink /J", ...)`,
   跨盘符时 warn + 跳过 + 不阻断
2. **copy**:对 `copy_paths` 每项用 `shutil.copytree(setup_dir/p,
   worktree/p, dirs_exist_ok=True)`;失败 warn + skip
3. **hooks**:在 worktree 内 `git config core.hooksPath ../_hooks/`(相
   对路径);自动建 `_hooks/` 共享目录(被 `link_paths` 默认排除,避免
   双 hooks 触发的循环)
4. **gitignore**:读 `<repo>/.gitignore`,缺 `.worktrees/` 行则 append

### D7: Fast-Path 恢复判定

**决策**:目录已存在时 3 步检查:

1. **`.git` file 存在**(git worktree 的标记,普通目录不会有)
2. **`git worktree list --porcelain` 能看到**
3. **`git status --porcelain` 返 dirty lines 数 == 0**

满足 → fast-path,跳过 `git worktree add`,直接调
`WorktreeManager.enter(name)` 把路径返回给 caller。任一不满足 → fallback
到正常路径(试图 `git worktree add`,走 D2 的失败 recovery)。

**依据**:
- 用户拍板"A. 检查 .git file + git worktree list 报 dirty"
- 看起来"健康"的常用条件:worktree 结构完整 + git 知道它 + 没脏文件
- 三步都 O(1) 成本(fs stat + subprocess),不需要重 git add
- 失败 fallback 让"目录意外残留"也能恢复

**实现**:

```python
async def _try_fast_path(self, name: str) -> WorktreeSpec | None:
    setup_dir = self.setup_dir
    worktree_dir = setup_dir / ".worktrees" / name
    if not worktree_dir.is_dir():
        return None
    git_file = worktree_dir / ".git"
    if not git_file.is_file():
        return None
    # git worktree list --porcelain 输出含 "worktree /path/to/dir"
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "list", "--porcelain",
        cwd=str(setup_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    listed = False
    for line in out.decode().splitlines():
        if line.startswith("worktree ") and line[len("worktree "):] == str(worktree_dir):
            listed = True
            break
    if not listed:
        return None
    # dirty check
    proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=str(worktree_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    if out.strip():
        return None  # 不干净就当 fallback 路径处理
    return WorktreeSpec(name=name, path=worktree_dir, branch=f"wt/{name}", state="active")
```

### D8: 退出保护 + force 决策

**决策**:`WorktreeManager.exit(name, force=False)` 决策树:

```
out_dir = setup_dir/.worktrees/<name>
if force == True:
    git worktree remove --force <out_dir> + git branch -D wt/<name>
    return Done

# 检查 1:未提交修改
git status --porcelain --untracked-files=all → 非空?
   是 → 保("dirty,需手工处理")
        return Detached(reason="uncommitted_changes")
# 检查 2:未推送 commit(branch 有 upstream 时)
if git rev-parse --verify --quiet @{u}:
    git log @{u}..HEAD → 非空?
       是 → 保("ahead of upstream,需手工处理")
            return Detached(reason="unpushed_commits")
# 都干净:
git worktree remove <out_dir> + git branch -d wt/<name>
return Removed
```

**依据**:
- 用户原话"有未提交修改或未推送 commit 默认拒绝删除"
- "force" 是给上层(TUI 手动 cancel / 用户明确放弃时)用的 escape hatch

**实现**:见 pseudo-code 上;真代码在 `worktree/manager.py:exit`。

### D9: 后台清理三层过滤

**决策**:`WorktreeCleanupDaemon.run_once()` 扫所有现存 worktree 目录,
三层过滤决定谁该清理:

1. **第 1 层 — 状态过滤**:`state in {"detached", "stale"}` 才考虑清理
   (`active` 跳过,`creating` 跳过)
2. **第 2 层 — 时间过滤**:`last_access > now - retention_minutes`
   跳过(太新不动)
3. **第 3 层 — 活跃关联过滤**:`name in SubAgentManager._tasks with
   state in {"running", "pending"}` 跳过(任务还活着)

任一过滤拒绝 → 跳过。**全部通过** → 调 `WorktreeManager.exit(name,
force=False)`,`status` 决定是否真删(参 D8)。

**依据**:
- 用户原话"后台定期清理过期的临时目录,三层过滤保证安全"
- 现有 v1.2 已经 `SubAgentManager._cleanup_expired` 模式
  (用于 task),worktree 复用类似 daemon

**实现**:`baozicode/worktree/cleanup.py:WorktreeCleanupDaemon`,
`asyncio.create_task(self._loop())` 在 `BaoZiCodeApp._on_mount` 末尾;
`_loop()`:`while True: run_once(); await asyncio.sleep(daemon_interval_seconds)`;
`_on_unmount` 调 `daemon.stop()`。

### D10: 缓存按绝对路径天然 key(无需新机制)

**决策**:**不引入新缓存层**。worktree sub-Agent 的 BuiltPrompt 与主
Agent 的 BuiltPrompt 因 `env_info` 段里的 cwd 不同而自然分裂;cache miss
是承认的取舍。

**依据**:
- 现有 v0.4 BuiltPrompt 缓存是按 byte-equality 命中;`env_info` 段已经
  渲染 `cwd` 字段(LT 看 `baozicode/prompt/sections/env_info.py` 验证)
- 不同 cwd → 不同 system prompt → Anthropic cache miss
- 失去 fork cache 优势换得文件隔离安全 —— 比 silence corruption 强
- 项目级 / user 级 memory(`.baozicode/BaoZiCode.md` /
  `~/.config/baozicode/memory/project/`) 都按 **project_root** 走,与
  cwd 无关 —— **天然被 fork / worktree 共享**,符合用户期望

**实现**:**无需实现**;这是设计保证,不需要新代码。

(注:`identity` 段也在 BuiltPrompt 里。如果用户想让主 Agent 跟 worktree
sub-Agent 共享更大段 cache,可以让 `env_info.cwd` 在 worktree sub-Agent
里渲染中性值(如 `<worktree>`)—— 这是 v1.4 优化,不在 v1.3 范围。)

### D11: Bash 的 worktree cwd 绑定(closure 注入)

**决策**:Bash 工具要知道 worktree cwd 是否合法,有两种注入方式:

A. **Bash.execute 每调用时 closure 查 worktree**(`app._is_inside_worktree`)
B. **Bash 工具按 project_root 多实例**,worktree 的 instance 在 spawn 时
   bind 一个有效的 root set

**选 A**:closure 注入,主 Agent / worktree sub-Agent 共享 Bash 工具模
块,实现最小。

**实现**:`baozicode/tools/bash.py` 加 module-level
`set_cwd_validator(callable)` 由 `SubAgentManager._run_subagent` 在
`asyncio.create_task(...)` 时 set,跑完 unset(tread 局部)。

```python
# baozicode/tools/bash.py
_cwd_validator: Callable[[Path], bool] | None = None

def set_cwd_validator(validator: Callable[[Path], bool]) -> None:
    module._cwd_validator = validator

# execute() 路径里调:
if not session._is_inside(resolved) and not (
    module._cwd_validator is not None and module._cwd_validator(resolved)
):
    return ToolResult.error_result(...)
```

(`EffectiveProjectRoot` 走 `SubAgentManager` 持有的 effective root set)

### D12: 不做的取舍

v1.3 **明确不做**:

- Worktree 之间合并策略(交给上层用 `git merge` 决定)—— 合并两个独立
  分支需要解决冲突、责任归属,不是 worktree 系统的责任
- 跨 worktree 代码同步(从主 repo `cp` 到 worktree 是 v1.4 / v2.0 候选)
  —— main repo 改动如何让 worktree sub-Agent 看到,是编辑器集成级别
  的问题
- 多 Agent 并行编排(谁先谁后由主 Agent LLM 决策)—— v1.3 提供
  `isolation` 设施,**让并行安全**,但不调谁先跑

这些是 v1.4 / future 阶段的 scope。
