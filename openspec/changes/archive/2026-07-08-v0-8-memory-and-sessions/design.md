# v0.8 Memory & Sessions — Design

## Context

BaoZiCode v0.7 落地后,Agent 跑长任务已经稳定(上下文压缩 + 五层防御 + MCP),
但**跨 session 的连续性**仍然为零:

```
启动 → 在项目根读 BaoZiCode.md → 跑任务 → 退出 → [一切归零]
       (单层硬编码)            (一次性)    (丢失)
```

三个相邻能力都缺:

1. **指令分层** — v0.4 引入的 `BaoZiCode.md` 是单层手写文件,没有
   `user_global` / `project_local` 优先级,不能 `@include` 复用片段,跨项目
   的个人偏好只能复制粘贴
2. **会话存档** — `ConversationManager` 纯内存,退出即丢;用户跑一个 30 轮
   任务,Ctrl+C 之后下次启动要从零开始
3. **自动长期记忆** — 模型知道"用户偏好 X"也只能在当轮说出来,没机会记到
   下次启动能看见的地方

v0.8 把这三件事一次补齐,技术约束严格遵循 spec:

- 启动时三层指令加载 → 注入 system prompt 顶部
- 每条 `add_*` 同步 append JSONL → 退出无损
- 自然停后异步调 LLM 抽笔记 → 用户下次启动 `MEMORY.md` 已被填好

```
   启动                    跑任务                       退出 / 下次启动
   ────                    ──────                       ─────────────
                          ┌────────────────┐
   读 3 层指令 ──────────▶│  Agent Loop    │
   读 MEMORY 索引 ───────▶│  (v0.7 主体)   │── add_* ──▶ JSONL append
                          │                │                │
                          │                │── 自然停 ──────▶ async 笔记更新
                          └────────────────┘                │
                                                           ▼
   ◀── 下次启动 ◀── sessions.list() ◀── 30 天外清理 ◀──────┘
       │
       └── resume(选 session) → 加载 JSONL → 修异常 → 灌 ConversationManager
```

约束(来自 spec + 已锁定的 10 个决策):

- **JSONL 追加写 + 不维护 meta** — 启动扫目录 + 解析首条拿 meta,少一份要
  同步状态
- **快照式异步笔记更新** — `create_task` 不阻塞,完成时核 session_id
- **三级分层超限** — 180/22KB 警告 → ≥200/25KB 后台 LLM 压缩(每会话最多
  1 次)→ 仍超限切人工
- **`YYYYMMDD-HHMMSS-xxxx` session ID** — v0.7 `uuid4()` 跟着迁移
- **BaoZiCode.md 不读现有 CLAUDE.md** — 两套独立指令体系
- **v0.4 的 `memory_path` 单文件字段** — 保留读,启动 warning + 下版废弃

## Goals / Non-Goals

**Goals:**

- 三层 `BaoZiCode.md` 按 user_global → `.baozicode/` → `project_root` 拼接,
  注入 system prompt 头部
- `@include` 路径深度 ≤ 5、visited set 防环、路径必须落在项目根或
  `~/.baozicode/`(拦截逃逸)
- 会话 ID `YYYYMMDD-HHMMSS-xxxx`,`xxxx` = `secrets.token_hex(2)`(同秒撞 +1)
- JSONL 同步 append,每行 `flush + fsync`,Windows / POSIX 都安全
- 启动时扫 sessions 目录,坏行跳过 + log warning,orphan tool_call 从那点截断
- Resume 时估算 token,超 `context_window - reserve` 自动跑一次
  `maybe_compact`(复用 v0.7 编排器)
- Resume 时首条 user message 与上次最后一条时间差 > 8 小时 → 插
  `<system-reminder type="time_gap">`
- 30 天外 JSONL 启动时清理;今天的文件永不删
- 4 类笔记 (`user-pref` / `correction` / `project` / `reference`) 各带 YAML
  frontmatter,Markdown body
- 双层 memory 目录(user + project),`MEMORY.md` 索引 ≤ 200 行 / 25 KB
- Agent.run 自然停(`COMPLETED` 或 `MAX_ITERATIONS_REACHED`)后异步调 LLM
- 笔记 prompt 输入近 5 轮对话 + 完整 memory index
- 笔记 prompt 输出 fenced JSON,parser 只取 ```json ... ``` 块
- 快照式并发:触发时冻 messages 快照,后台异步处理,完成时核 session_id
- 三级分层超限:① 180/22KB 警告 ② ≥200/25KB 后台 LLM 压缩(每会话 1 次)
  ③ 失败切人工(`/memory compress|prune`)
- v0.7 `uuid4()` session_id 迁移:启动检测 + rename + 撞号挂 `_legacy`

**Non-Goals:**

- 不做 session 之间的"知识继承"自动合并 — 每次新 session 都从 MEMORY.md
  开始,不在两个 session 之间搬运笔记
- 不做笔记的 embedding 检索 — 索引文件直接线性扫(200 行 25KB 很小)
- 不做用户级笔记的跨设备同步 — 留作 v0.9+ 议题(可能接 Git / iCloud)
- 不做 session 内 multi-agent 协作 — 单 session 单 Agent 仍是主路径
- 不做笔记的版本控制 — 写错就改错,不存 git history
- 不做 `/memory` 之外的笔记管理命令 — 新增/查看/压缩 3 个够了
- 不做 instruction 文件的热更新 — 改 BaoZiCode.md 后必须重启才生效
- 不动 v0.7 上下文压缩契约 — resume 借 `maybe_compact` 但不修改它
- 不动 v0.5 五层防御契约 — 笔记更新 LLM 调用走 `tools=[]`,无权限风险
- 不引 tiktoken / sentencepiece — 启发式估算沿用 v0.7

## Decisions

### D1. 三层指令加载顺序:user_global → .baozicode/ → project_root

**选择:** 按 `~/.baozicode/BaoZiCode.md` → `<project>/.baozicode/BaoZiCode.md` →
`<project_root>/BaoZiCode.md` 顺序拼接,后者内容在前者之后。LLM 看到拼接结果
时,**最先读到 user_global**(作为基础规则),最后读到 project_root(覆盖)。
冲突时 LLM 按"后写覆盖"语义处理。

**理由:** 对齐 Claude Code 习惯——user-global 提供"我永远遵守的规则",
project-local 提供"这个项目特殊规则"。spec 原文"项目级高于用户级"指
**优先级**,不是**拼接顺序**;Claude Code 的实际行为也是 user 在前
project 在后。

**替代方案:** project_root 排最前。**否决** — 跟 Claude Code 习惯相反,
跨项目用户配置时心智成本高,且 user_global 反而要"后写"才能生效,违反直觉。
**替代方案:** 三段独立渲染为三个 `<system-reminder>`。**否决** —
3 段独立片段会让 LLM 难以判断冲突解决路径,且占用更多 system prompt
token(每段带 boundary tag)。

### D2. @include:深度 ≤ 5 / visited 防环 / 路径拦截

**选择:** `@include <相对路径或绝对路径>` 指令在解析时:

- **深度限制 5** — 递归层数超过直接报 warning 并跳过该分支,不递归
- **visited set** — 用 `Path.resolve().as_posix()` 作 key,二次访问直接跳过
- **路径拦截** — 解析后的绝对路径必须 `is_relative_to(project_root)` 或
  `is_relative_to(Path("~/.baozicode/").expanduser())`,否则整条指令拒绝
  并 log warning
- **支持相对路径** — 相对路径以**当前文件所在目录**为 base,不是 cwd

**理由:** `instructions/include.py` 解析时这三道关一起做,任何一道失败就
跳过该 include 行并继续解析其他内容。深度限制防"include 自己 include
自己"的逻辑环,visited 防语义环(同文件被多条路径引用),路径拦截防恶意
指令读 `/etc/passwd` 之类敏感文件。

**替代方案:** 深度无限 + visited only。**否决** — 没有深度上限,恶意文件
可构造几千层 include 拖死启动。**替代方案:** 用 gitignore 风格 glob 排除。
**否决** — 黑白名单对"项目目录内任意文件"过于宽松,且跟 v0.5 黑名单的设计
哲学冲突。

### D3. Session ID:`YYYYMMDD-HHMMSS-xxxx`

**选择:** ID 格式 `YYYYMMDD-HHMMSS-xxxx`,`xxxx` = `secrets.token_hex(2)`
(4 hex chars,~65K 空间)。

```
生成算法:
  base = datetime.now().strftime("%Y%m%d-%H%M%S")
  suffix = secrets.token_hex(2)
  sid = f"{base}-{suffix}"
  # 撞号检查 + 兜底 +1:
  while (.baozicode/sessions/{sid}.jsonl).exists():
      sid = f"{base}-{secrets.token_hex(2)}"  # 重抽,极端情况
```

**理由:** 时间戳前缀让人能"一眼看出哪天",`xxxx` 后缀防同秒启动两个 CLI
撞号。`token_hex(2)` 比纯计数安全(后者在跨进程场景会撞),且只有 4 字符
不影响可读性。

**v0.7 迁移:** v0.7 的 `.baozicode/context/<uuid4hex>/` 目录启动时扫一遍,
对每个 uuid 目录读 `<uuid>/_meta.json` 拿创建时间戳,rename 成
`<YYYYMMDD-HHMMSS-xxxx>/`(撞号挂 `_legacy_<n>` 后缀)。同日多 session 不会
撞(原 uuid 路径已经分散)。

**替代方案:** 纯计数后缀。**否决** — 跨进程不安全,需要 flock 之类的
协调。**替代方案:** 完整 uuidv4。**否决** — 跟 spec 明确要求相反,且目录
名失去可读性。**替代方案:** `datetime + pid`。**否决** — pid 可重用
(进程退出后 pid 会被新进程继承)。

### D4. JSONL append 原子性:`f.flush() + os.fsync()`

**选择:** `SessionArchiver.append(msg)` 同步执行:

```python
def append(self, msg: Message) -> None:
    line = json.dumps(asdict(msg), ensure_ascii=False)
    with self._path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())  # POSIX + Windows 都支持
```

**理由:** POSIX 上小于 `PIPE_BUF`(4KB)的 `write` 是原子的;JSONL 单行
典型 < 2KB,本身就原子。Windows 上 `os.fsync()` 强制刷盘,但单文件多次
append 之间不被并发打断,只有进程崩在 `fsync` 之前那一瞬可能丢最后一行
(spec 明确"崩溃只丢最后一行")。

**替代方案:** 用 `aiofiles` 异步写。**否决** — 失去同步保证,且 `add_*`
本身是同步调用,异步落盘会让内存和 JSONL 状态不一致。**替代方案:** 批写
(攒 5 条一起 fsync)。**否决** — 进程退出时来不及 flush 的概率更高,
跟"崩溃只丢最后一行"的承诺冲突。

### D5. 不维护 meta 文件 — 启动扫目录 + 解析首条

**选择:** `SessionArchiver` 每次启动扫 `.baozicode/sessions/*.jsonl`:

```
SessionMeta(id, title, created_at, message_count, last_message_at):
  id       = 文件名去后缀 (YYYYMMDD-HHMMSS-xxxx)
  title    = 第一条 user message 前 60 字符
  created  = os.stat(path).st_mtime
  msg_count = 扫文件按行数(坏行跳过,不抛)
  last_msg_at = 最后一条 message 字段里的 timestamp
```

**理由:** 启动时全量扫目录(典型 < 100 sessions × 1 MB ≈ 50 ms,可接受);
O(N) 一次拿全。坏行 `try: json.loads / except: skip + warn`,绝不抛。

**替代方案:** 维护 `_index.json` 单独文件。**否决** — 每次 append 都要
双写,容易不同步;JSONL 单文件本身足够快。**替代方案:** SQLite。
**否决** — 增加二进制依赖,且 JSONL 本身人眼可读 + grep 友好。

### D6. Resume 异常处理四件套

**选择:** `load_session(id, root)` 启动时调一次,按顺序处理:

```
1) 坏行跳过
   try: json.loads(line)
   except JSONDecodeError: log.warning(...); continue

2) orphan tool_call 截断
   维护 tool_use_id set;读 tool_result 时检查其 tool_use_id 是否在 set 内,
   不在则从该位置截断,丢弃后续所有行,log warning("orphan tool_result dropped")

3) token 超限 → maybe_compact
   estimate_tokens(messages) > context_window - reserve:
       new_msgs, _ = await maybe_compact(messages, trigger="resume", ctx=ctx)
       灌回 ConversationManager

4) time_gap 提醒
   找到首条 user message 的 created_at,跟系统当前时间比较:
       gap = now - first_user_created
       if gap > 8 hours:
           在 messages[-2] 位置插 <system-reminder type="time_gap" ttl="once">
              "上次会话在 X 小时前,中间可能发生过上下文变化..."
```

**理由:** 四个异常独立,顺序处理无依赖。坏行跳过必须在最前(否则后续
parser 都被污染);orphan 截断在 token 估算前(否则算的是无效数据);
maybe_compact 在 time_gap 前(先压后插顺序确保 reminder 在最后位置)。

**替代方案:** 坏行直接拒加载。**否决** — 一个 corrupt byte 让整个 session
不可恢复,跟"JSONL 设计哲学"相反。**替代方案:** orphan tool_call 标记
成 error tool_result 喂回。**否决** — 等于污染对话,LLM 会看到幻觉
tool_call + 假 result。

### D7. 30 天过期清理:启动时跑一次

**选择:** `app.py` `__init__` 最后一步调
`sessions.cleanup(root, retention_days=30)`:

```
for sid_path in sessions_dir.iterdir():
    if not sid_path.is_file(): continue
    if (now - sid_path.stat().st_mtime).days > retention_days:
        sid_path.unlink()  # 静默删 + log info
        # 同时删对应的 .baozicode/context/<sid>/ (v0.7 兼容)
        context_dir = project_root / ".baozicode" / "context" / sid_path.stem
        if context_dir.exists(): shutil.rmtree(context_dir)
```

**理由:** 启动时跑一次,延迟可忽略(扫 100 个文件 stat 不超 50 ms);
今天的文件 mtime 距 now 永远 < 1 天,天然保护。失败不抛(整个 cleanup
包 try/except),只 log warning。

**替代方案:** 每次 append 后清理。**否决** — 高频路径增加不必要的
stat 开销。**替代方案:** cron 外部触发。**否决** — 跨平台不一致
(Windows 没 cron,WSL 又要单独配)。

### D8. 4 类笔记分类与 frontmatter schema

**选择:** 每个笔记文件 `<slug>.md`,frontmatter 强制字段:

```yaml
---
type: user-pref | correction | project | reference
created_at: 2026-07-08T15:30:00
source_session: 20260708-153000-a1b2
tags: [python, typing]
access_count: 3
last_accessed: 2026-07-08T18:00:00
---
# 笔记标题
正文 markdown 内容...
```

**4 类语义:**

- `user-pref`: 用户偏好(例:"用户喜欢 type hints 而不是 duck typing")
- `correction`: 纠正反馈(例:"用户说过不要用 emoji 回答")
- `project`: 项目知识(例:"这个项目用 uv 不是 poetry")
- `reference`: 参考资料(例:"test fixtures 在 tests/conftest.py")

**理由:** frontmatter 是元数据,markdown body 是内容;两者分离便于
LLM 处理(LLM 只读 body)和程序索引(只看 frontmatter)。`source_session`
用于追溯 + LRU 排序。`access_count` + `last_accessed` 给 `/memory prune`
LRU 用。

**替代方案:** 4 类分 4 个目录。**否决** — 物理分离让 LLM dedup 时要跨
目录读,体验差;一个目录 + type 字段即可。**替代方案:** JSON 文件不用
markdown。**否决** — markdown 可读、可被 git 渲染、可被 LLM 直接读全文,
JSON 反而割裂。

### D9. 双层 memory 目录:user + project

**选择:**

```
~/.baozicode/memory/              ← 用户级(跨项目生效)
├── MEMORY.md                     ← 索引文件(必须)
├── python-style.md               ← 单条笔记
├── user-pref-no-emoji.md
└── ...

<project>/.baozicode/memory/      ← 项目级(仅本项目)
├── MEMORY.md
├── project-uses-uv.md
└── ...
```

**理由:** 两层物理隔离避免"跨项目笔记污染";`MEMORY.md` 索引文件 200 行
/ 25KB 上限确保 Agent 启动时不会因笔记太多拖慢 prompt 构造。`MEMORY.md`
永远是索引的"权威单一来源",LLM 看到的也是它(不是整目录)。

**替代方案:** 单层 + namespace tag。**否决** — 物理隔离更直观,且
LRU / 清理 / 配额都可以按层分开做。**替代方案:** 不区分,所有笔记放一个
目录。**否决** — 用户级偏好和项目级知识混淆,resume 跨项目会带错笔记。

### D10. 笔记 LLM 输入:近 N 轮 + 完整 memory index

**选择:** `updater.update(messages_snapshot, store)` 调 LLM 时:

```
prompt input = {
  "recent_turns": messages_snapshot[-10:],  # 默认 N=5 轮 = 10 条 msg
  "user_memory_index": store.user.read_index(),
  "project_memory_index": store.project.read_index(),
  "system": NOTE_EXTRACTION_SYSTEM  # 固定模板
}
```

**理由:** 近 N 轮捕获"刚发生的对话"(用户刚说"我不喜欢 emoji");完整
index 提供"现有笔记作为 dedup 参考"。近 5 轮对应 ~2-5K token,index
满限 200 行 / 25KB ≈ 7K token,总输入 ~10K token — 在合理范围。

**替代方案:** 全部对话 + 全部笔记原文。**否决** — 100 轮任务
conversation 已经爆,且笔记全文比索引贵 10 倍。**替代方案:** 仅近 N 轮
无 index。**否决** — dedup 无参考,LLM 容易重复建笔记。

### D11. 笔记输出格式:fenced JSON

**选择:** LLM 输出格式:

```
[LLM 可选思考文字...]

```json
{
  "operations": [
    {"action": "add", "type": "user-pref", "slug": "no-emoji", "title": "用户偏好无 emoji", "content": "..."},
    {"action": "update", "path": "user-pref-no-emoji.md", "content": "..."},
    {"action": "delete", "path": "obsolete-note.md"}
  ],
  "index_update": true
}
```
```

**parser:** 用正则 ` ```json\n(.*?)\n``` ` 非贪婪匹配,捕获块后
`json.loads` 解析。找不到 fenced block 或 JSON 解析失败 → 整次更新
判失败,fire `record_update_failure`,等待下次自然停再触发。

**理由:** 外层允许 LLM 先写思考(提高 JSON 准确性,跟 v0.7 摘要的
`---ANALYSIS---` 思路一致);内层 fenced JSON 给 parser 一个明确边界,
误判率低。parser 三态:成功 / 解析失败 / fenced block 缺失 — 三态各自
有 metric,容易诊断。

**替代方案:** 纯 JSON 输出。**否决** — LLM 容易在 JSON 前后加解释文字
污染 parser。**替代方案:** YAML frontmatter 块。**否决** — YAML 解析
对 LLM 输出格式更宽容,反而不利于严格 dedup。

### D12. 笔记更新并发:快照式

**选择:** `Agent.run()` 内 `COMPLETED|MAX_ITERATIONS_REACHED` 触发点:

```python
# 主循环 COMPLETED 分支:
terminate_reason = StopReason.COMPLETED
self._memory_snapshot = list(self._conversation.to_list())  # 浅拷贝 messages
asyncio.create_task(self._memory_updater.update(self._memory_snapshot))
break
# 注意:_memory_snapshot 是 frozen 列表,后台任务拿的是引用但内容不变
```

`_memory_updater.update(snapshot)` 完成后:

```python
async def update(self, snapshot):
    operations = await self._llm_extract(snapshot)
    if operations is None:
        record_update_failure(...)
        return
    # 写入磁盘前核 session_id:
    if self._session_id != current_session_id():
        log.info("session changed during update, abort")
        return
    self._store.apply(operations)
```

**理由:** 浅拷贝 messages 列表(每条 Message 是 frozen dataclass,浅拷贝
安全);`create_task` 不阻塞主循环;`current_session_id()` 防止"用户
跑完一轮笔记更新还没好,又开新 session"的污染。

**替代方案:** 排他锁,更新期间冻结主循环。**否决** — 用户感到 1-3 秒
卡顿,体验差。**替代方案:** 纯 fire-and-forget,不核 session_id。**否决** —
有 race condition:用户 `/new` 开新 session 时旧 session 的笔记更新会
写到错的目录。

### D13. 笔记触发范围:COMPLETED + MAX_ITERATIONS_REACHED

**选择:** 仅这两种 `StopReason` 触发笔记更新,不触发 `USER_CANCELLED` /
`STREAM_ERROR` / `COMPACTION_FAILED` 等异常终止。

**理由:** COMPLETED 是"模型自然完成"的语义;MAX_ITERATIONS_REACHED
虽然不自然,但跑满上限的对话里通常有值得记的事。USER_CANCELLED 通常
是用户在半句话上 ESC,不完整,记笔记会污染。STREAM_ERROR / COMPACTION_FAILED
是技术异常,跟用户偏好/项目知识无关。

**替代方案:** 三种都触发(加 USER_CANCELLED)。**否决** — 用户取消
可能正在生气(例:"不要这样做!"),记下来可能适得其反。**替代方案:**
仅 COMPLETED。**否决** — 跑满上限的对话被浪费,反而是高频高价值场景
(用户跑了 20 轮 LLM 用光了,大概率有值得记的事)。

### D14. 三级分层超限:警告 → 自动 LLM 压缩 → 人工

**选择:** `overflow.check_and_act(store, session_limiter)` 在每次
`apply()` 之后调:

```
状态机:
  STATE_NORMAL       (lines < 180, bytes < 22 KB)
  STATE_WARN         (180 ≤ lines < 200 或 22 KB ≤ bytes < 25 KB)
  STATE_AUTO_COMPRESS (lines ≥ max_lines 或 bytes ≥ max_bytes,且 session_limiter < auto_compress_per_session)
  STATE_HUMAN_NEEDED (压缩后仍超限 OR session_limiter ≥ auto_compress_per_session)
```

```
STATE_NORMAL       → 无操作
STATE_WARN         → log yellow warning,提示用户 /memory compress
STATE_AUTO_COMPRESS → 后台 asyncio.create_task(_compress(store)),
                      session_limiter += 1
STATE_HUMAN_NEEDED → log red error,弹 <system-reminder type="memory_overflow">
                     (sticky,每次笔记更新重复提醒),不自动修改索引
```

**LLM 压缩 prompt 强制约束:**

```
要求:
1. 禁止删除任何完整主题,只合并重复笔记 / 精简文字
2. 保留所有唯一业务记录、关键时间节点
3. 压缩后目标体积 ≤ 阈值 70%
4. 输出 fenced JSON 操作列表(同 D11 格式)
```

**递归防护:** `session_limiter` 记录本会话已自动压缩次数,达 1 后
任何再超限都直接进 STATE_HUMAN_NEEDED。

**理由:** 三级分层避免"无限 LLM 压缩递归"——LLM 压缩本身可能因为笔记
太多超过窗口,失败后必须有兜底。人工介入通过 `/memory compress`
(再次手动深度压缩) 或 `/memory prune` (LRU 按 `last_accessed` 排序删旧)。

**替代方案:** 仅 LRU 自动清理。**否决** — 删错笔记不可逆,用户
没机会 review。**替代方案:** 仅 LLM 压缩无递归防护。**否决** —
LLM 可能因为窗口不够死循环,耗 token。

### D15. v0.4 `memory_path` 字段兼容

**选择:** v0.8 不删字段,但加 Pydantic `Field(deprecated=True, ...)`,
`config/loader.py` 检测到该字段非默认时:

1. **仍能读** — `prompt/sections/memory.py` 在双层 `MEMORY.md` 都空时,
   fallback 到 `memory_path` 单文件读取
2. **启动 warning** — CLI banner 打印一行 `WARN: memory_path is deprecated,
   move to <user_dir>/MEMORY.md + <project_dir>/MEMORY.md`
3. **下版废弃预告** — CHANGELOG 写明 v0.9 删除字段

**理由:** 老用户升级不破流程,只是看到一行 warning。仍能读保证 v0.7
行为可见,v0.9 之后彻底切干净。

**替代方案:** 硬废弃报错。**否决** — 老用户升级直接挂,体验差。
**替代方案:** 完全静默 fallback。**否决** — 用户不知道字段已废弃,
永远不迁移。

### D16. BaoZiCode.md 不读现有 CLAUDE.md

**选择:** v0.8 引入独立的 `BaoZiCode.md` 文件名,**不读取**项目根目录
现有的 `CLAUDE.md`。CLAUDE.md 留给 Claude Code 自己读这个项目时用,
BaoZiCode.md 是 BaoZiCode 的专属项目指令。

**缺失降级:** 启动扫描发现三层文件全部不存在 → 静默跳过 + banner 一句
"未找到 BaoZiCode.md,建议创建项目根目录文件"。

**理由:** 两套独立指令体系避免命名冲突(BaoZiCode 用户写的指令不会
影响 Claude Code 读项目时的行为,反之亦然)。且名字"风格一致" —
`BaoZiCode.md` 是给 BaoZiCode 的,`CLAUDE.md` 是给 Claude 的。

**替代方案:** 共用 CLAUDE.md。**否决** — 用户偏好和项目规范混在一起,
语义不清。**替代方案:** BaoZiCode.md + 同时读 CLAUDE.md 作为 fallback。
**否决** — 优先级混乱(谁覆盖谁?用户搞不清),且增加 cognitive load。

### D17. 时间跨度阈值:默认 8 小时

**选择:** `AgentConfig.time_gap_threshold_hours: int = 8`。

**理由:** 8 小时覆盖"午休 / 过夜 / 周末"三种常见跨 session 场景,且
不会太敏感(用户在 4 小时内来回切 session 不想看到提醒)。

**替代方案:** 4 小时。**否决** — 短 session 间隔不需要反复提醒。
**替代方案:** 24 小时。**否决** — 用户隔夜回来完全无 context 重建提示。

### D18. v0.7 session_id 迁移

**选择:** v0.8 启动时扫 `.baozicode/context/`:

```python
for old_path in (context_dir.iterdir()):
    if old_path.is_dir() and is_uuid4_hex(old_path.name):
        # 读 v0.7 _meta.json 拿 created_at
        meta_path = old_path / "_meta.json"
        if meta_path.exists():
            ts = json.loads(meta_path.read_text())["created_at"]
        else:
            ts = old_path.stat().st_mtime
        new_id = format_ts_as_id(ts)  # YYYYMMDD-HHMMSS-xxxx
        new_path = context_dir / new_id
        if new_path.exists():
            new_path = context_dir / f"{new_id}_legacy"
        old_path.rename(new_path)
```

**理由:** 启动一次扫描 + rename,延迟可忽略。撞号(同日 v0.7 已有该时间戳)
挂 `_legacy`,人工后续决定如何合并。

**替代方案:** 不迁移,留 v0.7 uuid 目录。**否决** — 两套目录并存增加
cleanup 复杂度,且 v0.7 临时目录本身就该被 v0.8 接管。**替代方案:**
扔掉老 uuid 目录重建。**否决** — 丢 offload 缓存,下次启动需要重新 offload
所有大块,浪费 token。

## Open Questions

1. **笔记 LLM 调用是否走 cache 通道?** 当前选择**不走**(用临时 system
   串 + `cache_breakpoints=None`),理由跟 v0.7 摘要一致:近 N 轮对话变化
   频繁,缓存命中率低。等 v0.9+ 引入更精细 cache 策略再考虑。

2. **`MEMORY.md` 索引文件被人手改坏了怎么办?** 当前选择**信任用户**(Lstat
   失败 → log warning + 用空索引 fallback)。不自动备份,不自动修复。
   用户自己管理。可选 Phase 8 加 git-versioned 备份,但本期不做。

3. **跨 session 的笔记共享策略?** 当前选择**双层物理隔离**(user / project
   目录分开),不主动搬运。Phase 7 时再考虑是否需要"项目级笔记升级到
   user_global"的命令。

4. **笔记 dedup 的 LLM 误判怎么办?** 当前选择**用户人工 review** —— 笔记
   创建后 `/memory` 可查;不自动合并重复,避免 LLM 把有价值但写法不同的
   笔记合并掉。Phase 8 可加 `/memory dedup` 手动触发命令。

5. **并发 CLI(同项目跑两个 baozicode)写 sessions 怎么办?** 当前选择
   **`secrets.token_hex(2)` 4 hex 撞率 ~1/65K,接受小概率撞号**。极端情况
   (同秒 + 同 4 hex):第二个 CLI 启动时发现 sid 已存在会重新生成。可考虑
   flock,但本期不做,留作 v0.9+。

## Risks / Trade-offs

**[R1] Windows JSONL append 原子性**
→ Mitigation: `f.flush() + os.fsync()` 强制刷盘。Windows 上 `os.fsync()`
   行为是 flush file buffers 到磁盘,POSIX 上是 page cache → disk,都满足
   "进程崩只丢最后一行"的承诺。测试用模拟进程被杀场景验证。

**[R2] v0.7 uuid 目录迁移撞号**
→ Mitigation: 同时间戳已有新目录时挂 `_legacy_<n>` 后缀,人工后续决定。
   启动 log 列出所有迁移操作,用户可 review。

**[R3] 笔记 LLM token 成本**
→ Mitigation: 输入 ~10K token(近 5 轮 + index),输出 ~500 token(操作
   列表),每 session 触发 1-3 次,日均成本约 30-100K token。`/status`
   暴露笔记更新触发次数 + 累计 token。

**[R4] @include 路径拦截的边界**
→ Mitigation: `is_relative_to(project_root) OR is_relative_to(user_baozicode)`
   双白名单,绝对路径不允许(用户写 `/etc/xxx` 直接拒)。visited set
   用 `Path.resolve().as_posix()` 防止 symlink trick。

**[R5] 笔记 prompt 不走 cache 通道**
→ Mitigation: 跟 v0.7 摘要一致,直接 `cache_breakpoints=None`。命中率低
   收益小,徒增复杂度。

**[R6] 跨 session 笔记污染**
→ Mitigation: user / project 物理隔离两目录,`source_session` 字段记录
   笔记来源 session,`/memory` 命令分两栏显示,UI 上区分颜色。

**[R7] 笔记 dedup LLM 误判**
→ Mitigation: 不自动合并,只增不改(避免误删)。`update` 操作仅限
   "扩展已有笔记内容",不允许"删除已有笔记其他段落"。`delete` 操作
   需要 source_session = current(防止误删早期 session 创建的笔记)。

**[R8] 三级分层超限的递归防护漏洞**
→ Mitigation: `session_limiter` 每会话单调递增,达 1 后任何再超限直接
   进 STATE_HUMAN_NEEDED。同时 `apply()` 失败时不递增 limiter,失败
   重试不受限制(避免误锁)。

**[R9] memory dir bootstrap 时权限**
→ Mitigation: `store.bootstrap()` 先 `mkdir(parents=True, exist_ok=True)`,
   失败时 log error + 跳过该层,不阻塞启动。Linux/macOS 默认 umask 077
   确保 user-global 目录用户独占。

**[R10] 时间跨度提醒的归属**
→ Mitigation: `<system-reminder type="time_gap" ttl="once">` 走 user-role,
   跟其他 reminder(v0.4 `env` / v0.4 `plan_mode` / v0.5 `denial_rate_limit`)
   保持一致,在 `_inject_reminders` 里统一注入 `messages[-2]` 位置。

**[R11] Agent.run 内 `create_task` 的事件循环依赖**
→ Mitigation: Agent 必须在 asyncio event loop 内运行(v0.3 已要求),
   `create_task` 安全。如果用户在 sync 上下文调 `agent.run()` 启动
   session,旧代码路径就已经挂了,v0.8 不解决这个旧问题。

## Migration Plan

**Phase 1:instructions 包 + 三层 BaoZiCode.md 加载**

- `baozicode/instructions/loader.py` — 三层文件扫描
- `baozicode/instructions/include.py` — `@include` 递归解析
- `baozicode/instructions/schema.py` — 数据类
- `baozicode/instructions/__init__.py` — `bootstrap()`
- 单元测试:三层扫描 / `@include` 5 种异常 / 拼接顺序 / 空目录降级
- **完成标志:** 启动有 banner + Agent 看到 system prompt 顶部拼接内容

**Phase 2:memory store(CRUD + frontmatter + index 渲染)**

- `baozicode/memory/store.py` — 笔记 CRUD + 索引读写 + frontmatter 解析
- `baozicode/memory/schema.py` — `Note` / `NoteType` / `MemoryIndex`
- `baozicode/memory/__init__.py` — `MemoryStore` 类
- 单元测试:CRUD / frontmatter 解析 / index 拼接 / 双层物理隔离
- **完成标志:** `/memory` 命令可查,但还没自动更新

**Phase 3:sessions archiver + resume**

- `baozicode/sessions/archive.py` — `SessionArchiver.append()`
- `baozicode/sessions/resume.py` — `load_session()` 异常处理四件套
- `baozicode/sessions/cleanup.py` — 30 天过期清理
- `baozicode/sessions/schema.py` — 数据类
- `baozicode/sessions/__init__.py` — `list_sessions()` / `bootstrap()`
- 集成 v0.7 `maybe_compact` —— resume 时 token 超限自动调一次
- 单元测试:JSONL append / 坏行 / orphan / time_gap / 30 天清理
- **完成标志:** `/resume` 命令可用,跨 session 续接正确

**Phase 4:v0.7 session_id 迁移**

- `baozicode/context/orchestrator.py` — `session_id = YYYYMMDD-HHMMSS-xxxx`
- `baozicode/app.py` `__init__` — 加 v0.7 uuid 目录迁移步骤
- 单元测试:uuid 检测 / rename / 撞号挂 _legacy
- **完成标志:** 启动 log 列出迁移操作,无破坏

**Phase 5:Agent.run hookpoints(JSONL append + reminder 注入)**

- `baozicode/agent/loop.py` — `add_*` 后调 archiver.append()
- `baozicode/conversation/manager.py` — 接受 archiver 回调
- `baozicode/agent/loop.py` `_inject_reminders` — 加 `time_gap` /
  `memory_refreshed` reminder 类型
- 单元测试:hookpoint 触发顺序 / reminder 注入位置 / 双 hookpoint 协调
- **完成标志:** 跑一个 session,JSONL 文件随之增长

**Phase 6:memory updater + overflow 三级分层**

- `baozicode/memory/updater.py` — 异步 LLM 编排 + 快照式并发
- `baozicode/memory/overflow.py` — 三级分层状态机
- `baozicode/memory/prompt.py` — 笔记提取 prompt 模板
- 单元测试:LLM 笔记更新(mock)/ 快照式 race condition / 三级分层状态机
- **完成标志:** 自然停后笔记文件被自动创建

**Phase 7:CLI/TUI 集成**

- `baozicode/cli.py` — banner 加 sessions 摘要 + `--resume <id>` + `--new`
- `baozicode/tui/chat_screen.py` — `/resume` `/memory` `/new` slash 命令
- `baozicode/app.py` — bootstrap 顺序调整
- 集成测试:启动 → 跑对话 → 退出 → resume 端到端
- **完成标志:** `baozicode` 无参弹选择器,`--resume <id>` 直接续

Rollback: 每 Phase 独立提交 + 独立 revert;`MemoryConfig` /
`SessionConfig` 加 `enabled: bool = True` 字段,设 False 时整层
新模块禁用,Agent 退回 v0.7 行为。