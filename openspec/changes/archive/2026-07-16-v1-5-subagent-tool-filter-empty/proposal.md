## Why

v1.2 SubAgent 的 `ToolFilter`(`baozicode/agents/filter.py`)把"过滤后空集"当成错误,抛 `ToolFilterEmptyError`。但 builtin `summarizer` 角色的设计意图就是"无工具纯 LLM 调用"(`tools-deny=[ALL]` 拒绝全部 7 个内置工具),filter 走完 L1+L3 后必然空集 → summarizer 永远派不出去。

这是**定义/实现不一致**:角色想表达"我要空工具集",filter 解读成"配错了"。在 smoke test `tests/test_subagent_smoke.py` 中已复现。

## What Changes

- `ToolFilter.visible_tools` 在 L2 产出空集时,若 `role.tools` 是显式空列表 `[]` 则放行(visible_tools = [])。L3 让集合变空仍按"配置冲突"报错
- 区分 `role.tools is None`(无约束,L2 跳过)vs `role.tools == []`(显式要空,L2 产出空 = OK)
- builtin `summarizer` 角色 frontmatter 从 `tools-deny: [Write, Edit, Bash, Read, Grep, Glob, WebFetch]` 改写为 `tools: []`,语义从"拒绝所有"变成"我要空工具集",body 补一句说明
- 增单测 `tests/agents/test_filter_empty_allowed.py` 覆盖三种情形

**BREAKING**: 无。`summarizer` 从不可用变为可用;其他角色行为不变。

## Capabilities

### New Capabilities

(无 — 这是现有 filter 语义的细化,放进 modified spec)

### Modified Capabilities

- `subagent-manager`:增一条 Requirement 描述"显式空工具集"语义 + Scenario 覆盖三种 filter 情形

## Impact

- `baozicode/agents/filter.py` — 改 `visible_tools` cached_property,加分支判断
- `baozicode/agents/builtin/summarizer/AGENT.md`(或等价的 markdown 文件)— frontmatter 改写
- `tests/agents/test_filter_empty_allowed.py` — 新增单测
- `openspec/specs/subagent-manager/spec.md` — 加 Requirement + 3 个 Scenario