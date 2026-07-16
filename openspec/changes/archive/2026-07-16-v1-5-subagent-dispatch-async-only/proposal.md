## Why

`SubAgentManager.dispatch()` 暴露 `async_=False` 同步路径(走 `_dispatch_sync_blocking`),在从 `asyncio.run()` 或 running event loop 内调用时,代码走 `if loop.is_running(): return loop.create_task(_runner())` 分支,**直接返回 Task 对象而不是字符串结果**,调用方拿到 Task 后没人 await,导致 task 永远 pending、调用方卡住或抛 `RuntimeError('async generator ignored GeneratorExit')`。

生产代码零调用点(grep 全 repo 无 `async_=False`),同步路径是**死路径**。同时 sync 在 running loop 里阻塞主 loop 是 anti-pattern,设计本身就不该支持。

## What Changes

- `SubAgentManager.dispatch()` 收到 `async_=False` 时直接抛 `NotImplementedError`,msg 说明「v1.5 起 async_ 必须是 True」
- 删除 `_dispatch_sync_blocking` 方法
- 删除 `task_executor` wrapper(`_do_dispatch`)里 sync 分支处理
- 更新 module docstring 移除「async_=False → str(完成摘要)」描述
- 加单测覆盖 `async_=False` 抛 `NotImplementedError`

**BREAKING**: 是。任何调用方传 `async_=False` 会立即抛异常。已 grep 确认生产代码零调用点,无实际影响。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `subagent-manager`:在「派发 API」相关 Requirement 加一条:async_=False 自 v1.5 起被禁用,async_=True 是唯一路径

## Impact

- `baozicode/agents/manager.py` — 删 `_dispatch_sync_blocking`,改 `dispatch()` 入口校验,删 `_do_dispatch` sync 分支
- `baozicode/agents/manager.py` docstring + module docstring — 同步更新
- `tests/agents/test_dispatch_async_only.py` — 新增单测
- `tests/test_subagent_smoke.py` — 加一个断言:async_=False 抛 `NotImplementedError`
- `openspec/specs/subagent-manager/spec.md` — 加 Requirement + Scenario