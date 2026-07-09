## Context

v1.1.1 polish commit `49eed65` 把 hooks 系统承诺的 3 类 runtime state(slots + audit +
clear 隔离)落地,留了 7 项 v1.2 polish。本 change 取其中 4 项关键:

| # | 项 | 状态(v1.1.1 收尾时) | v1.2 动作 |
|---|---|---|---|
| 1 | system.compaction fire | `maybe_compact` 入口没 fire 事件 | 接通 hook dispatcher |
| 2 | system.cancel fire | `USER_CANCELLED` 设置后没 fire | 接通 hook dispatcher |
| 3 | clear_sticky_reminders action | schema 不存在 | 新增 ActionYaml kind + executor |
| 4 | clear_stable_system_overrides action | schema 不存在 | 新增 ActionYaml kind + executor |
| 5 | TUI ToolResult 颜色按 execution_status | 单一色 | tool_card.py 分支渲染 |

剩余 3 项留后续(fsync / final pipeline invocation / warn-log block_* + denied_by=None),
按 process 推到下个 polish 版本,不在本 change scope。

约束(与 v1.1.1 同):
- **零 breaking change** — 配置 / API / 数据格式不动
- **不引入新 capability** — 全是 `hooks-lifecycle` capability 内部增量
- **spec 必须先于代码** — OpenSpec spec-driven 流程要求 4 个新 / 修改 requirement 先在 specs/ 落地
- **保持 fail-open 行为** — hook 失败 / fire 异常只 log.warning,不打断 Agent / 压缩 / 取消流程

## Goals / Non-Goals

**Goals:**
1. system.compaction / system.cancel 在各自时机可靠 fire,用户可 hook 进压缩 / 取消做自动化
2. 新增 2 个 control 类 action(kind: clear_sticky_reminders / clear_stable_system_overrides),
   语义独立 — 各自只清一类 hook 注入状态
3. TUI tool_card 渲染时按 execution_status 上色,用户一眼区分 5 种工具结局
4. 全量回归 + 新测试覆盖

**Non-Goals:**
- 不做 fsync interval(fsync 是 OS-level,对 session audit 是 over-engineering)
- 不做 final `action_kind="pipeline"` invocation(每条 hook 自己写 invocation 已足够)
- 不做 warn-log block_* + denied_by=None(目前是 silent-pass,pipeline 内部一致性已经靠 freeze 校验保证)
- 不动 sub-agent 实际执行(还是 v1.1 占位)
- 不动 hooks 系统的对外 API 签名
- 不动 pipeline 顺序 / ToolResult 已有字段

## Decisions

### D1: system.compaction fire 时机

**决策**:在 `baozicode/context/orchestrator.py:maybe_compact(...)` 函数入口处
**开始压缩前**(Layer 2 摘要调用 LLM 之前)fire `system.compaction` 事件。fire 完继续正常跑 maybe_compact。

**为什么入口而不是完成时**:
- 用户 hook 的典型场景是"压缩前先存档 / 暂停其他自动化",fire 入口给用户反应窗口
- 完成后 fire 太晚,hook 拿到的是已发生的 final state,影响操作
- v1.1 spec 在 Event taxonomy 表里说 system.compaction 是 "When `maybe_compact` triggers (start or end)" — 两可,选 start 更实用

**payload**:用 `CompactionTrigger` 字段(dict 形式,trigger / tokens_before / estimated_after)。

**dispatcher 为 None** → skip fire(向后兼容,无 hooks 配置的 v1.0 / v1.1 项目不挂)。

**实现要点**:
```python
# orchestrator.py:maybe_compact 入口
def maybe_compact(messages, trigger, ctx):
    # fire system.compaction hook(start)
    if hasattr(ctx, "hook_dispatcher") and ctx.hook_dispatcher is not None:
        try:
            ctx.hook_dispatcher.run("system.compaction", {
                "trigger": trigger,
                "tokens_before": len(messages),
            })
        except Exception as exc:
            log.warning("hook system.compaction 异常: %s", exc)
    # ... 原有压缩逻辑
```

### D2: system.cancel fire 时机

**决策**:在 `baozicode/agent/loop.py:Agent.run` 主循环检测到 USER_CANCELLED 设置时
**退出前** fire `system.cancel` 事件。

**为什么退出前而不是 cancel 信号设置时**:
- cancel 信号设置后 run 循环可能还有 in-flight 任务在执行,fire 早了 hook 看不到 in-flight 状态
- 退出前 fire,hook 看到的是"取消已确认,准备退出"的稳定状态
- 让 hook 有机会清理 in-flight resource(关 HTTP 连接、写一行收尾日志等)

**payload**:`{"reason": "user_cancelled", "iteration": <current_iteration>}`。

**dispatcher 为 None** → skip fire。

**实现要点**:
```python
# agent/loop.py:run 主循环末尾(USER_CANCELLED 退出路径)
if stop_reason == StopReason.USER_CANCELLED and self._hook_dispatcher is not None:
    try:
        self._hook_dispatcher.run("system.cancel", {
            "reason": "user_cancelled",
            "iteration": iteration,
        })
    except Exception as exc:
        log.warning("hook system.cancel 异常: %s", exc)
```

### D3: clear_sticky_reminders / clear_stable_system_overrides action 形态

**决策**:新增两个 ActionYaml kind,各自独立。语义按用户确认:
- `clear_sticky_reminders`:只清 `Agent._pending_reminders`(sticky `hook_prompt` reminder 队列)
- `clear_stable_system_overrides`:只清 `Agent._hook_stable_overrides`(钉在 stable_system 末尾的 `## Hook Overrides` 段)

**为什么不清 `_temp_reminders`**:
- temp 本意 turn-scoped,下轮 `_inject_reminders` 自动消费即清,无需显式 clear action
- 用户选"语义独立分开",明确说不动 temp

**为什么 2 个 action 而不是合并 1 个**:
- 用户决策明确选"分开"
- 组合场景下用户能精确控制(比如 turn.start 时只清 sticky,turn.end 时只清 stable)

**action 字段**:
```yaml
- action: clear_sticky_reminders
  # 无其他字段(只引用 agent 上的 _pending_reminders)
```

**实现要点**:
- executor 直接 `setattr(agent, "_pending_reminders", [])` 或 `_hook_stable_overrides = []`
- 不复用 v1.1.1 的 `clear_hook_runtime_state` helper(那个清 3 项,semantic 过宽)

### D4: TUI tool_card 颜色映射

**决策**:`baozicode/tui/tool_card.py:ToolResultCard` 渲染时按 `execution_status` 选 CSS class:
| execution_status | 颜色 | 含义 |
|---|---|---|
| `block_l1` | red | L1 黑名单硬拦截 |
| `block_hook_pre` | yellow | hook.pre 拒 |
| `block_permission` | orange | L2-L5 拒 |
| `executed_success` | green | 工具成功 |
| `executed_failed` | red | 工具自身抛错 |
| `None`(向后兼容) | default | v1.0 旧 ToolResult,无 execution_status |

**为什么 5 种而非合并**:
- L1 红 + hook_pre 黄 + L2-L5 橙 三色区分"被谁拒",对调试关键
- success 绿 + failed 红 是通用习惯

**实现要点**:
```python
# tool_card.py
EXEC_STATUS_CLASS = {
    "block_l1": "-block-l1",
    "block_hook_pre": "-block-hook-pre",
    "block_permission": "-block-permission",
    "executed_success": "-executed-success",
    "executed_failed": "-executed-failed",
}

def compose(self):
    klass = EXEC_STATUS_CLASS.get(self.result.execution_status, "")
    return Container(..., classes=f"tool-result-card {klass}".strip())
```

styles.tcss 加 5 个 class。

**TUI snapshot 测试**:
- 不用 PIL/textual screenshot(脆),改用 class 断言 — 直接读 `render` 输出的 widget
  classes 列表(或者构造 widget 后查 `classes` 属性)
- 测试 5 个 status + 1 个 None 各 1 个 case

### D5: 测试结构

**决策**:所有新测试追加到现有 `tests/test_hooks_v11.py`(复用 fixture)。

新增 6 个测试:
1. `test_system_compaction_event_fires_on_maybe_compact` — spy dispatcher,调 maybe_compact,断言收到 system.compaction
2. `test_system_cancel_event_fires_on_user_cancel` — Agent.run 触发 USER_CANCELLED,断言收到 system.cancel
3. `test_clear_sticky_reminders_action_empties_pending_reminders` — executor 跑 action 后 _pending_reminders == []
4. `test_clear_stable_system_overrides_action_empties_overrides` — executor 跑 action 后 _hook_stable_overrides == []
5. `test_clear_actions_do_not_touch_other_hook_state` — 跑 clear_sticky 验证 _hook_stable_overrides 不动 + _temp_reminders 不动
6. `test_tool_card_renders_color_per_execution_status` — 构造 5 种 ToolResult,渲染 widget,断言 classes 包含预期 class

## Risks / Trade-offs

[R1] **`maybe_compact` 入口 fire system.compaction 会让 hook 在压缩 LLM 调用前抢 CPU 时间** → Mitigation:hook 默认 fail-open,跑长任务不会 block 压缩;HookDispatcher.run 同步超时由 hook.timeout_seconds 控制(默认 30s,够长)。如果发现真 block,future work 改 async fire。

[R2] **system.cancel fire 在 USER_CANCELLED 退出前,可能 hook 抛异常导致退出失败** → Mitigation:try/except 包整个 fire 块,异常仅 log.warning。这是 spec 的 fail-open 要求,跟其他 hook fire 一致。

[R3] **clear action 直接 setattr Agent 私有字段,触犯封装** → Mitigation:跟 v1.1.1 `clear_hook_runtime_state` helper 同样的 trade-off — polish 阶段的最快路径。v1.3 评估是否提为 `agent.clear_pending_reminders()` / `agent.clear_hook_overrides()` 公开 API。

[R4] **TUI 颜色按 execution_status 但用户没装 styles.tcss 某些 class,会 fallback 到默认** → Mitigation:styles.tcss 加 default 兜底 class,任何 execution_status 至少有一个 class 命中。

[R5] **2 个新 action kind 改 ActionYaml discriminator,现有 YAML 配置如果 action 拼写错可能 schema validation 行为变化** → Mitigation:Pydantic discriminator 用 Literal,拼写错就抛 ValidationError,跟原来一致,无回归。

## Migration Plan

无 breaking change → 无需 migration。

部署:
1. 合并 `v1-2-hooks-polish` → main
2. bump version 1.1.1 → 1.2.0(若项目有版本号)
3. release notes 写一句"system events 可 hook + 新 2 个 control action + TUI 颜色"

回滚:
- 单 commit revert
- 配置文件不动(action YAML 新字段可省略,默认行为不变)
- TUI 颜色回退:旧版本 ToolResult 不带 execution_status,新版走 default class(等价旧行为)

## Open Questions

- [ ] **system.cancel 的 payload 字段**:目前定 `reason` + `iteration`,是否加 `tool_call_id`(若有 in-flight tool_call)?等实现时看 agent loop 的状态再决定
- [ ] **TUI 颜色具体 hex/textual CSS 变量**:留给 tool_card.py 实施时定,设计决策里只锁 5 种语义色名
- [ ] **clear action 是否要支持条件分支(只清特定 hook_id 的 sticky reminder)**:当前定"全清一类",future 可加 `hook_id_filter` 字段,但本 polish 不做