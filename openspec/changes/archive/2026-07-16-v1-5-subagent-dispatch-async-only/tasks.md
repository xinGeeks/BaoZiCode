## 1. SubAgentManager 改造

- [x] 1.1 在 `baozicode/agents/manager.py` 的 `dispatch()` 方法入口加 `if not async_: raise NotImplementedError(...)`
- [x] 1.2 删除 `_dispatch_sync_blocking` 整个方法(包括其 docstring)
- [x] 1.3 删 `dispatch()` 里 `if async_: ... else: return self._dispatch_sync_blocking(...)` 的 else 分支,简化为单一 async 路径
- [x] 1.4 删 `task_executor` / `_do_dispatch` 里 sync 分支处理(`if async_: return ... else: return ...` 改为只留 async 分支)
- [x] 1.5 更新 module docstring 里「async=False:await 阻塞到完成」一段为「async_=False 自 v1.5 起禁用,抛 NotImplementedError」
- [x] 1.6 更新 `dispatch()` docstring 删 `async_=False → str(完成摘要)` 那段

## 2. 单测覆盖

- [x] 2.1 新建 `tests/agents/test_dispatch_async_only.py`
- [x] 2.2 测 1:`await manager.dispatch(async_=False, ...)` 抛 `NotImplementedError`,msg 含 "v1.5" 或 "async_=True"
- [x] 2.3 测 2:`SubAgentManager` 类无 `_dispatch_sync_blocking` 属性(hasattr 断言 False)
- [x] 2.4 测 3:扩展 `tests/test_subagent_smoke.py`,加一个 `async_=False` 抛异常断言(独立 test 块,不阻塞现有 happy path)
- [x] 2.5 测 4:`task_executor` 收到 `async=False` 时(从 LLM 角度走 tool)走异常路径(单元覆盖)

## 3. 验证

- [x] 3.1 跑 `pytest tests/agents/test_dispatch_async_only.py -v` 全过
- [x] 3.2 跑 `pytest tests/test_subagent_smoke.py -v` 全过(含新加的 async_=False 断言)
- [x] 3.3 跑 `pytest tests/agents/ -v` 无回归
- [x] 3.4 grep 全 repo 确认无 `async_=False` 残留(除单测里故意触发的)