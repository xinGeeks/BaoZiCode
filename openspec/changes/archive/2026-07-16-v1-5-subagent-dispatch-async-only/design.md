## Context

`baozicode/agents/manager.py` 当前 `dispatch()` 是个 `async def`,接收 `async_: bool = True` 参数。当 `async_=True`(默认)时,`dispatch()` 创 `asyncio.create_task(self._run_subagent(task))` 后台跑,返回 task_id。当 `async_=False` 时,走 `_dispatch_sync_blocking(task, timeout_seconds)`,期望阻塞等结果。

`_dispatch_sync_blocking` 内部:

```python
try:
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # 主 loop 在跑(常见) — 起一个临时 task 等
        return loop.create_task(_runner())  # ← bug
    return loop.run_until_complete(_runner())
except RuntimeError:
    return asyncio.run(_runner())
```

注释说"起一个临时 task 等",但 `loop.create_task(_runner())` 只是注册 task,实际没人 await。返回 `Task` 对象而不是字符串。

实际触发条件:从 `asyncio.run(main())` 内调 `dispatch(async_=False)`,`asyncio.get_event_loop()` 返回当前 running loop,触发 buggy 分支。tests/test_subagent_smoke.py 第一版就是这个症状。

## Goals / Non-Goals

**Goals:**
- 删同步路径,`async_=True` 成为唯一合法值
- `async_=False` 调用方立即拿到 `NotImplementedError`,而不是卡死或返回错误对象
- `dispatch()` 函数签名保持兼容(不删 `async_` 参数,只是把 `False` 改成 throw)

**Non-Goals:**
- 不改 `dispatch()` 的 async 路径(已通过测试)
- 不改 `_run_subagent` / `_on_subagent_done` 行为
- 不改 `task_executor` async 分支(sync 分支删)
- 不改 `TASK_TOOL` schema 描述(LLM 永远传 async_=True)

## Decisions

### D1: `async_=False` 抛 `NotImplementedError`

```python
async def dispatch(self, ..., async_: bool = True, ...):
    ...
    if not async_:
        raise NotImplementedError(
            "SubAgentManager.dispatch(async_=False) 自 v1.5 起被禁用,"
            "请使用 async_=True 并轮询 task.state / 监听 idle notification。"
        )
    # 否则走原 async 路径
```

**Rationale**: 用 `NotImplementedError` 而不是 `ValueError` 更准确 — 这个功能是"已知未实现"而不是"参数无效"。调用方立刻知道这是个 API 移除而不是传错参数。

### D2: 删除 `_dispatch_sync_blocking` 整个方法

不再保留方法体,直接 `del`。理由:死路径 + 修复成本高于移除收益。

### D3: `task_executor` 的 sync 分支删除

`task_executor` 当前的 sync 分支(在 `_do_dispatch` 里)依赖 sync dispatch。删了之后,`task_executor` 只剩 async=True 路径。LLM 永远传 `async_=True`,无功能影响。

### D4: 文档同步

`manager.py` module docstring 当前写「async=False:await 阻塞到完成(或 timeout demote)」 — 改为「async_=False 自 v1.5 起禁用,见 NotImplementedError」。

`dispatch()` docstring 当前三段返回值说明 (`async=True → str(task_id)`, `async=False → str(...)`) 删 `async=False` 那段。

## Risks / Trade-offs

- [Risk] 第三方扩展(罕见)可能传 `async_=False` → **Mitigation**: 立刻抛 `NotImplementedError`,fail-fast 不卡死;grep 已确认生产代码无调用
- [Risk] 测试代码 / 文档示例可能写 `async_=False` → **Mitigation**: 改 docs + 更新现有测试(本提案任务里包含)
- [Risk] TUI 代码如果未来要加同步派发会失去能力 → **Mitigation**: TUI 跑在 running loop 里,本身就 anti-pattern;真要同步结果用 `await dispatch(...)` 即可

## Migration Plan

无迁移步骤。改完即生效:
1. 调用 `dispatch(async_=False)` → `NotImplementedError`,不卡死
2. 调用 `dispatch(async_=True)` → 不变
3. 现有生产代码全走 async_=True,零影响

## Open Questions

(无 — 决策已锁)