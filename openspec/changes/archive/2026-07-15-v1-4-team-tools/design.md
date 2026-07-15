# v1.4 Team Lead — Collaboration Tools Design

## Data Model

### Task (新增)

```python
@dataclass(frozen=True)
class Task:
    """共享任务清单的一个条目。"""

    id: str                                 # uuid4 短码 8 字符,tasks.jsonl 内唯一
    body: str                               # 任务描述(plain text,可分多行)
    status: TaskStatus                      # pending / ready / in_progress / done / failed / canceled
    depends_on: tuple[str, ...] = ()        # 依赖的 task id 列表(空 = 无依赖)
    assignee: str | None = None             # 派给哪个 member(空 = 未派)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None      # 第一次 in_progress 的时间
    completed_at: datetime | None = None    # done / failed / canceled 的时间
    error: str | None = None                # 失败原因(仅 failed 时填)

TaskStatus = Literal[
    "pending",    # 已创建,deps 未满足或刚派
    "ready",      # deps 全部 done,fitness 等于可派(尚未分配 assignee)
    "in_progress", # 已被某个 member 接管
    "done",       # 成功完成
    "failed",     # 失败 (member 上报 error)
    "canceled",   # 显式取消
]
```

**为什么用 tuple 而不是 list 做 depends_on**:frozen dataclass 要求 hashable,
tuple 自然 hashable,list 必须 `field(default_factory=list)`。tuple 让 Task
可作为 dict key(set/dict lookup 等价)。

**为什么 status 有 6 个状态而非简化版**:Lead LLM 经常需要"找所有 ready 派
活"、"看所有 failed 决定补救",6 状态正好覆盖;合并 2 个会丢失信息。

### tasks.jsonl 文件格式

```
<teams_dir>/<team_name>/tasks.jsonl
└──(JSONL append-only,一行一个 Task.to_dict())
```

JSON 行格式:`{id, body, status, depends_on, assignee, created_at,
started_at, completed_at, error}`,全字段 snake_case,timestamp ISO 8601
UTC。

**为什么独立 lockfile**:`team_dir/.tasks.lock` 与 mailbox `.lock` 分开 —
tasks.jsonl 锁竞争模式(Lead LLM 单写)与 mailbox 不同(多进程并发),分开
避免 Lead LLM 单次决定被 member inbox 锁阻塞。

### Message 协议扩展(复用 Mailbox 既有字段,无 schema 变)

```
member outbox 第 1 行:
  sender: alice
  body: ---PLAN-3f4a7c---
        我会先改 foo.py 的 X 函数(...)
        ---END---
  metadata=None,仅靠 body 文本前缀识别

Lead inbox 给 alice:
  sender: lead
  body: APPROVED: 3f4a7c
  或
  body: REJECTED: 3f4a7c 不要改 X,只做 bar

member outbox 上报完成:
  sender: alice
  body: ---TASK-COMPLETE-<task_id>---
        done: <简短摘要>
        ---END---

member outbox 上报失败:
  sender: alice
  body: ---TASK-FAILED-<task_id>---
        error: <错误>
        ---END---
```

**为什么不用 structured JSON metadata**:Message dataclass 当前 schema 只
有 `sender / body / timestamp / read / summary`,无 metadata 字段。为不
破坏 foundation,协议走 plain text + `---XXX-...---`分隔符,`summary` 字
段留给 LLM 自动摘要用。

### Approval flow trace(完整示例)

```
时间线:
T0: Lead LLM 调 team_task_create("写 health_check 路由")
    → tasks.jsonl: {id:t-001, status:pending, body:..., assignee:null}
T1: Lead LLM 调 team_dispatch(team=devops, member=alice, task_id=t-001)
    → alice.inbox: {sender: lead, body: "task=t-001: 写 health_check 路由"}
    → alice.wake.signal: touched
    → 返回 ToolResult(content="dispatched alice task=t-001")
T2: alice agent loop 醒来,从 inbox 读 t-001
T3: alice 看 t-001 涉及修改公共 API → 检查 member.requires_approval=True
T4: alice 写 plan → alice.outbox:
        {sender: alice, body: "---PLAN-3f4a7c---\n我会先加 route 然后加 handler ...\n---END---"}
    → alice.state.json: status="waiting"
T5: MailboxNotifier 跑(Lead Agent 下一轮之前)
    → 扫 alice.outbox 找到 PLAN-3f4a7c
    → Lead conversation 注入:
        <system-reminder type="team_mailbox">
        alice (waiting since T4) 提交 plan 3f4a7c:
        我会先加 route 然后加 handler ...
        回复格式:APPROVED: 3f4a7c 或 REJECTED: 3f4a7c <reason>
        </system-reminder>
T6: Lead LLM 看 sys-reminder,判定合理
    → 调 team_send_message(team=devops, member=alice,
                            body="APPROVED: 3f4a7c")
    → alice.inbox 新行 {sender: lead, body: "APPROVED: 3f4a7c"}
    → alice.wake.signal: touched
T7: alice agent loop 醒来读 inbox 看到 APPROVED: 3f4a7c
    → 匹配 plan_id 与自己发出的
    → state.json: status="running", current_task=t-001
    → 开始干活
T8: alice 干完,写 outbox:
        {sender: alice, body: "---TASK-COMPLETE-t-001---\ndone: 写了 route 与 handler,加了 3 个测试\n---END---"}
    → state.json: status="idle", current_task=None
T9: MailboxNotifier 跑
    → 找到 TASK-COMPLETE
    → tasks.jsonl: {t-001: status=in_progress → done, completed_at=now}
    → Lead 注入 sys-reminder: alice 完成 t-001
```

## Tool Surface(6 个 ToolDefinition)

### `team_dispatch`

```json
{
  "name": "team_dispatch",
  "description": "把一个 task 派给一个 team member。member 必须已 add_member(...);若 member.requires_approval=True,member 会先回 plan 给 Lead 等审批。返回 ToolResult 含 member 接受的 task_id。",
  "risk": "low",
  "side_effect": true,           // 写 inbox + state.json + wake.signal
  "path_args": [],
  "role_visibility": ["lead"],
  "parameters": {
    "type": "object",
    "properties": {
      "team":  {"type": "string",  "description": "team 名;必须在 TeamsRegistry 里"},
      "member": {"type": "string", "description": "member 名;已 add_member(...) 注册;LLM 必显式传(不允许 'auto')"},
      "task_id": {"type": ["string","null"], "description": "可选关联到 tasks.jsonl 里的某个 task(可选,若不传则只发消息)"}
    },
    "required": ["team", "member"]
  }
}
```

Executor 行为:
1. `TeamsRegistry.get(team)` 校验存在(`TeamNotFound` → error result)
2. `TeamStore.get_member(team, member)` 校验存在(`MemberNotFound` → error)
3. 校验 `member.state.status != 'offline'` 或自动 spawn(后台 pane / coroutine)— 实际 spawn 由 pane-backend proposal 负责;foundation 阶段如果 offline 报错让 Lead 先 `team use`
4. `Mailbox.append_message(member_dir, "inbox", Message(sender="lead",
   body=f"task={task_id}: {body_or_ref}"))`
5. `Mailbox.touch_wake(member_dir)`
6. 若 task_id 非空且 tasks.jsonl 有该 task:更新 `status: pending → in_progress`, `assignee=member`, `started_at=now`
7. 返回 `ToolResult(content="dispatched <member> task=<task_id>")`

### `team_send_message`

```json
{
  "name": "team_send_message",
  "description": "给已存在的 team member 发纯文本消息(非任务派活)。用法广泛:approve/reject plan / 问进度 / 广播 / 注入上下文。",
  "parameters": {
    "type": "object",
    "properties": {
      "team":  {"type": "string"},
      "member": {"type": "string"},
      "body":  {"type": "string", "description": "消息正文,可含 APPROVED:/REJECTED: 前缀触发审批语义"}
    },
    "required": ["team", "member", "body"]
  }
}
```

Executor:`Mailbox.append_message + touch_wake`,无 task 关联。

### `team_cancel`

```json
{
  "name": "team_cancel",
  "description": "终止一个 member 的当前任务循环(软取消 — 写 cancel 消息等 member idle);或传 terminate=True 强杀后端进程(SIGTERM pane / cancel coroutine)。",
  "parameters": {
    "type": "object",
    "properties": {
      "team":  {"type": "string"},
      "member": {"type": "string"},
      "reason": {"type": "string", "description": "取消原因,写入 member outbox 供审计"},
      "terminate": {"type": "boolean", "default": false, "description": "true=杀后端进程;false=只取消当前任务"}
    },
    "required": ["team", "member"]
  }
}
```

Executor:
- `terminate=False`:`Mailbox.append_message(inbox, Message(sender="lead",
  body=f"CANCEL: {reason}"))` + 更新 tasks.jsonl 当前 task 为 canceled
- `terminate=True`:写 inbox cancel + `Mailbox.write_state(state=
  status='offline')` + 后端 kill(pane-backend proposal 注入实际
  signal,foundation 阶段若 backend=coroutine 直接 `asyncio.Task.
  cancel()`)

### `team_merge`

```json
{
  "name": "team_merge",
  "description": "把 team 内所有 member 的 worktree 分支(默认 wt/<name>)顺序合并到 target 分支(默认 main)。冲突走 git merge --abort + 报错;已合并的保留。",
  "parameters": {
    "type": "object",
    "properties": {
      "team":   {"type": "string"},
      "target": {"type": "string", "default": "main", "description": "目标分支名,默认 main"},
      "dry_run": {"type": "boolean", "default": false, "description": "true=只输出 would-merge 计划,不实际跑 git"}
    },
    "required": ["team"]
  }
}
```

Executor(`run_team_merge`):
1. 校验 project_root 是 git repo (`subprocess.run("git rev-parse")` exit=0)
2. `git -C project_root checkout <target>`(若失败 → error)
3. 字典序遍历 `team.members`,对每个 member:`git -C project_root merge
   --no-ff wt/<name> -m "Merge wt/<name> from team <team>"`
4. 冲突捕获(returncode != 0):`git -C project_root merge --abort` +
   收集 abort 列表
5. 返回 `ToolResult(content=json.dumps({"merged": [...], "aborted":
   [{"member":"bob","reason":"conflict in foo.py"}], "target":"main"}))`

### `team_task_create`

```json
{
  "name": "team_task_create",
  "description": "在共享 tasks.jsonl 创建新任务,可指定 depends_on 表达 DAG 依赖。返回任务 id(8 字符短码)。",
  "parameters": {
    "type": "object",
    "properties": {
      "team":  {"type": "string"},
      "body":  {"type": "string", "description": "任务描述,plain text"},
      "depends_on": {
        "type": "array",
        "items": {"type": "string"},
        "default": [],
        "description": "本任务依赖的 task id 列表;空数组表示无依赖"
      },
      "auto_ready": {
        "type": "boolean",
        "default": true,
        "description": "若 deps 全部 satisfied,自动 mark ready;否则 pending"
      }
    },
    "required": ["team", "body"]
  }
}
```

Executor:
1. `tasks_lock(team_dir)` + 生成 `id=secrets.token_hex(4)`(8 字符)
2. `Tasks.append(team_dir, Task(id, body, status=pending/ready 根据 auto_ready,
   depends_on=tuple(deps)))`
3. 若 ready 触发:追加 `<system-reminder type="team_task">` 给 Lead
4. 返回 `ToolResult(content="created task=<id> status=<pending|ready>")`

### `team_task_query`

```json
{
  "name": "team_task_query",
  "description": "查共享任务清单。支持按 status / assignee / depends_on 过滤,返回 list of task(id, status, body 摘要, assignee, dependencies)。",
  "parameters": {
    "type": "object",
    "properties": {
      "team": {"type": "string"},
      "status_filter": {
        "type": "array",
        "items": {
          "enum": ["pending","ready","in_progress","done","failed","canceled"]
        },
        "default": [],
        "description": "空数组 = 所有状态"
      },
      "assignee": {
        "type": ["string","null"],
        "default": null,
        "description": "null=查所有;字符串=只查该 member 的"
      },
      "include_ready_graph": {
        "type": "boolean",
        "default": false,
        "description": "true=返回每任务的 depends_on + ready_for_dispatch 布尔"
      }
    },
    "required": ["team"]
  }
}
```

Executor:
1. `Tasks.read_all(team_dir)`(读整 JSONL,坏行跳过)
2. 按 status / assignee 过滤
3. 若 include_ready_graph:`for task in result: task["ready_for_dispatch"]
   = (task.status == "pending" AND all deps in done/skipped)`
4. 返回 `ToolResult(content=json.dumps(result, indent=2))`

## Tasks 文件层

```python
class Tasks:
    """共享任务清单 — 类比 Mailbox,但无方向(direction)。"""

    @staticmethod
    def append(
        team_dir: Path,
        task: Task,
        *,
        lock_timeout: float = 5.0,
        lock_stale_seconds: float = 30.0,
    ) -> None:
        """原子追加一条 task(写临时 + fsync + os.replace)。

        用 write-then-rename 而非 append(任务有 id 唯一性约束,append 会
        容忍重复写)。
        """
        lock_path = team_dir / ".tasks.lock"
        with mailbox_lock(lock_path, timeout=..., stale=...):
            target = team_dir / "tasks.jsonl"
            tmp = team_dir / f".tasks.jsonl.{os.getpid()}.{rand}"
            try:
                tmp.write_text(task.to_json_line() + "\n", encoding="utf-8")
                tmp.flush()
                os.fsync(tmp.fileno())
                with open(target, "a", encoding="utf-8") as dst:
                    with open(tmp, "r", encoding="utf-8") as src:
                        shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
            finally:
                try: tmp.unlink()
                except FileNotFoundError: pass

    @staticmethod
    def read_all(
        team_dir: Path,
        *,
        skip_bad_lines: bool = True,
    ) -> list[Task]:
        """读整个 tasks.jsonl,坏行跳过,按 append 时序返回。"""

    @staticmethod
    def update_status(
        team_dir: Path,
        task_id: str,
        new_status: TaskStatus,
        *,
        assignee: str | None = None,
        error: str | None = None,
    ) -> bool:
        """读 all → 修改匹配 task 的 status / assignee / completed_at /
        error → atomic write(target.replace)整个文件。

        **关键**:同 lock 下做,因为 Lead LLM 单写,但要保证读-改-写
        期间没有并发 append(否则覆盖丢失)。**必须**在 tasks_lock
        内做整文件 read-modify-replace;不用 append-only 模式。

        Returns: True if found and updated, False if not found.
        """

    @staticmethod
    def find_ready(team_dir: Path) -> list[str]:
        """返回当前所有 status=pending 但所有 deps=done 的 task id。
        用于 team_dispatch 时挑选可派任务。
        """

    @staticmethod
    def detect_cycles(team_dir: Path) -> list[list[str]]:
        """DFS 检测 tasks.jsonl 内是否有环依赖(depends_on 自指 / 互指)。"""
```

**为什么 update_status 不是 append-only**:tasks.jsonl 是 append-only,单
纯 append 会产生多份相同 id 的 task。status 字段必须**就地更新**(read 全
部 → 改 → 覆盖写)。配合 lockfile 保证一致性。

**为什么不增量 Kahn 一气呵成**:N 通常 < 20(`Lead LLM 拆 10 个 task` 数
量级),N² OK。Threshold 改 algorithm 是 [R1] 的 mitigation,代码里留
helper 不必现在实现。

## Approval Protocol

```python
class ApprovalProtocol:
    """PLAN-/APPROVED:/REJECTED: 解析与发送 helper。"""

    @staticmethod
    def parse_plan(body: str) -> tuple[str, str] | None:
        """解析 body 找 `---PLAN-<id>--- ... ---END---` 块。

        Returns: (plan_id, plan_body) | None(无 plan block)
        """

    @staticmethod
    def parse_approval(body: str) -> tuple[str, Literal["approve","reject"], str | None] | None:
        """解析 body 找 `APPROVED: <id>` 或 `REJECTED: <id> <reason?>`。

        Returns: (plan_id, action, reason|None) | None
        """

    @staticmethod
    def send_approval(
        inbox_dir: Path,
        plan_id: str,
        action: Literal["approve", "reject"],
        reason: str | None = None,
    ) -> None:
        """构造 `APPROVED: <id>` / `REJECTED: <id> <reason>` Message 写
        inbox_dir + touch wake。

        Mailbox.append_message + Mailbox.touch_wake 的封装。
        """
```

**为什么纯基于 mailbox**:不引入新文件类型 / 不引入新锁 / 不引入新协
议层;Lead 看 outbox、Lead 写 inbox,完全复用 foundation 既有 API。
tradeoff 是 plan 内容(body)是 plain text,LLM parse 但不是强 schema —
接受这个工程妥协,plan 是自然语言为主,JSONL 强 schema 反而束缚 LLM
表达。

## MailboxNotifier

```python
class MailboxNotifier:
    """Agent Loop 钩子 — 每轮 Agent 决策前扫所有 member outbox 摘要
    转 system-reminder。"""

    def __init__(self, teams_registry: TeamsRegistry, team_name: str):
        self._registry = teams_registry
        self._team_name = team_name
        self._seen_message_hashes: set[str] = set()  # 防止重复注入

    def build_reminder(self) -> str | None:
        """扫所有 member outbox,提取 task_complete / plan / error,
        返回组装好的 <system-reminder type=\"team_mailbox\"> 块;若
        无新消息 → 返回 None。

        内容示例:
          <system-reminder type=\"team_mailbox\">
          alice (idle) completed task=t-001 at 14:32
            摘要: 写了 route 与 handler,加了 3 个测试
          bob (waiting since 18:01) submitted plan 9d2e3f:
            我会先重构 baz 模块再写测试 ...
          carol (running, last_active 5min ago) — 无新消息
          </system-reminder>
        """
```

**为什么去重 `seen_message_hashes`**:MailboxNotifier 每轮 Agent 决策
前跑,无 dedup 会反复注入同一 plan/complete。`hash(body + timestamp)`
作 dedup key,Agent.run() 跨轮持久(self._seen_message_hashes 存在
self 上)。

**为什么不在 Lead conversation 永久保留 mailbox 内容**:Lead conversation
会爆。MailboxNotifier 把内容转**单轮** sys-reminder,Lead LLM 看完决策
后即可丢弃;永久内容由 member outbox 文件承担,Lead 必要时可调
`team_send_message` 给 member(因 member 可读自己 inbox / outbox)。

## Member State Transitions

```
             ┌──────────────────────┐
             │     offline          │ (未启动 / 已 terminate)
             └─────────▲────────────┘
                       │ team_cancel(terminate=True)
                       │ 或 pane backend 退
             ┌─────────┴────────────┐
             │       idle           │ ←── 初始(add_member)+ 后启动完成 / 任务完成
             └─────────▲────────────┘
                       │ team_dispatch(team=..., member=...)
             ┌─────────┴────────────┐
             │      running         │ (member.requires_approval=False 立即进)
             │                      │
             │   或 ┌────────────┐  │
             │      │   waiting  │◀─┴─ member.requires_approval=True 派活后
             │      └─────▲──────┘    │
             │            │           │
             │            │ APPROVED: │ REJECTED: 直接 reject 持续
             │            │           │
             └────────────┴───────────┘
                  task 完成 / 失败 / cancel → idle / offline
```

转换触发器:
- `add_member` 创建 → `offline`
- pane backend 启动成功 → `idle`(本 proposal 不实现,pane-backend 实现)
- `team_dispatch(member)` 且 `requires_approval=False` → `running`
- `team_dispatch(member)` 且 `requires_approval=True` → `waiting`
- `MailboxNotifier` 看到 `TASK-COMPLETE-*` outbox → `idle`
- `MailboxNotifier` 看到 `TASK-FAILED-*` outbox → `idle`,tasks.jsonl 该 task → `failed`
- `team_cancel(terminate=False)` → `idle`(停止当前任务,变 ready 接 next)
- `team_cancel(terminate=True)` → `offline`

## Role Visibility 过滤

```python
# tools/base.py
@dataclass
class ToolDefinition:
    # ... 既有字段 ...
    role_visibility: list[str] | None = None
    # None = 所有 role 可见
    # ['lead'] = 仅 lead
    # ['lead', 'coordinator'] = multiple
```

```python
# tools/registry.py
class ToolRegistry:
    def get_all_tools(
        self,
        role: str | None = None,
    ) -> list[ToolDefinition]:
        """按 role 过滤工具列表。

        - role=None → 返全部(老路径,向后兼容)
        - role='lead' → role_visibility is None OR 'lead' in role_visibility
        - 其他 role 同样
        """
        all_tools = list(self._builtin_tools) + list(self._mcp_tools.values())
        if role is None:
            return all_tools
        return [
            t for t in all_tools
            if t.role_visibility is None or role in t.role_visibility
        ]
```

```python
# agent/loop.py
class Agent:
    def __init__(
        self,
        *,
        role: Literal["lead", "member", "subagent"] = "subagent",
        # ... 其他
    ):
        self._role = role
        # ...其他 init...

    def _build_available_tools(self) -> list[ToolDefinition]:
        return self._tool_registry.get_all_tools(role=self._role)
```

**为什么默认 role='subagent' 而不是 'lead'**:向后兼容 — v1.3 老
Agent 构造代码不传也对(继续走 subagent),Lead Agent 在
`BaoZiCodeApp` 显式传 `role='lead'` 才能拿到 team_*。

## Test Matrix

| 模块                   | 单元测试覆盖                                              |
|------------------------|-----------------------------------------------------------|
| Task dataclass         | frozen / JSON round-trip / 6 status 字面量                |
| Tasks.append           | happy / 并发锁 / crash 容错                                |
| Tasks.update_status    | 单 thread 改字段 / 找不到 task / 并发安全                   |
| Tasks.detect_cycles    | 无环 / 自环 / 二元环 / 三元环                              |
| Tasks.find_ready       | 全 deps done / 任一 dep pending / 自身就是 dep            |
| team_dispatch executor | member 不存在 / task 不存在 / 写 inbox + wake / 自动改 task |
| team_send_message      | 写 inbox + wake / 纯文本不限 / APPROVED: prefix 不解析     |
| team_cancel            | soft cancel 写 inbox / terminate 改 state offline         |
| team_merge             | 顺序合 / 冲突 abort / dry_run / 空 team                    |
| team_task_create       | append + 8 字符 id 唯一 / auto_ready 自动 status           |
| team_task_query        | status filter / assignee filter / ready_graph 计算正确     |
| ApprovalProtocol       | PLAN 解析 / APPROVED 解析 / send_approval 写 inbox         |
| MailboxNotifier        | 去重 / plan 注入 / complete 注入 / 多 member 同时活跃      |
| role_visibility filter | lead 拿到 team_* / subagent 拿不到 / member 拿不到         |
| Agent.role             | 默认 subagent / Lead 显式 / Member 显式                    |

合计约 `~71` 个新测试。

## 与 pane-backend / coordinator proposal 的接口约定

team-tools 不实现 pane spawn / watchdog / coordinator 双锁,但**为本
proposal 留以下接口**:

```python
# teams/store.py:TeamStore (foundation 已有,扩展)
class TeamStore:
    # ... 既有方法 ...

    def get_backend_handle(self, member_name: str) -> BackendHandle:
        """返回 Member 的 BackendType 对应的实际句柄(tmux session /
        iTerm2 window / Windows Terminal tab / coroutine task)。

        pane-backend proposal 实现;team-tools 不实现,但
        team_dispatch / team_cancel 通过它判 backend 类型决定怎么
        spawn / kill。
        """

# teams/store.py: 新增 Protocol(pane-backend 实现 fulfill,coroutine
# backend 也实现)
class BackendHandle(Protocol):
    def pid(self) -> int | None: ...
    def kill(self, *, grace_seconds: float = 5.0) -> None: ...
    def is_alive(self) -> bool: ...
```

team-tools 在 `team_dispatch` / `team_cancel(terminate=True)` 里调
`backend_handle.pid()` / `kill()`;若 backend pane-backend 还没注入
(foundation 阶段),fallback 到 "no-op + log warn"(不破坏)。

coordinator proposal 加 `role='lead-coordinator'` 派生 + 工具白名单收
缩;team-tools 已经在 `role_visibility` 上铺好路。

## 已锁定的 12 个决策(全部由 team-tools 落地的部分)

| #  | 决策                                       | 本 proposal 落地                                           |
|----|--------------------------------------------|------------------------------------------------------------|
| 1  | Team 长期固定 + 多次复用                    | team_dispatch 反复可调,team-state 持久在 team.json        |
| 2  | Lead team_dispatch(name="alice") 必填       | parameters.required 强制 member field                       |
| 3  | 跨 worktree 路径走 L2 sandbox 白名单        | team_tools 不写文件(只 mailbox);v1.3 L2 不需扩展          |
| 4  | P2P 消息 = 名称注册表 + 邮箱文件            | team_send_message 写 inbox + wake;touch + wait_for_wake    |
| 5  | Wake = Lead touch 信号 + 200ms watchdog    | MailboxNotifier 实现上层协议;底层 wake.signal foundation 已有 |
| 6  | Idle 仅 task 完成触发                       | MemberAgent 写 TASK-COMPLETE-* outbox → MailboxNotifier 改 state |
| 7  | Approval = YAML frontmatter 格式            | 走 mailbox + APPROVED:/REJECTED: 协议(详情见上)            |
| 8  | Tasks 文件 teams/<t>/tasks.jsonl           | Tasks.append / read_all / update_status 实现              |
| 9  | Mailbox 持久化层(决策对应)                  | foundation 已实现,本 proposal 只用                          |
| 10 | 消息格式(s/t/r/sum 字段)                   | foundation 已实现,本 proposal 只用                          |
| 11 | Lead 终止 = 主 Loop 自驱到底                | MemberAgent 不会主动 die;Lead 用 team_cancel 显式终止      |
| 12 | Resume 加载(决策对应)                      | foundation Mailbox.wait_for_wake 抽象已立;pane-backend 填充 |

本 proposal 落 1 / 2 / 4 / 5 / 6 / 7 / 8 / 11 共 8 个决策;3 由
foundation L2 已有 + 9 / 10 由 foundation 已有;12 由 pane-backend 填充。
