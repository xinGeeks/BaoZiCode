## 1. Filter 改造

- [x] 1.1 在 `baozicode/agents/filter.py` 的 `ToolFilter.visible_tools` 里加 `explicit_empty` 状态跟踪(L2 检测 `role.tools is not None and role.tools == []` 时设为 True)
- [x] 1.2 修改末尾空集检查:`if not final and not explicit_empty` → raise `ToolFilterEmptyError`;`if explicit_empty` → return `[]`
- [x] 1.3 改 `ToolFilter.layer_states` 暴露 `L2_explicit_empty: bool`,便于调试

## 2. summarizer frontmatter 改写

- [x] 2.1 改 `baozicode/agents/builtin/summarizer/AGENT.md`:把 `tools-deny: [Write, Edit, Bash, Read, Grep, Glob, WebFetch]` 替换为 `tools: []`
- [x] 2.2 body 段补一句说明「这是无工具角色,`tools: []` 是显式声明」

## 3. 单测覆盖

- [x] 3.1 新建 `tests/agents/test_filter_empty_allowed.py`
- [x] 3.2 测 1:`tools=None` 时 filter 产出非空(全放行 minus task)
- [x] 3.3 测 2:`tools=[]` 时 filter 产出 `[]` 不抛异常
- [x] 3.4 测 3:`tools=["Read"]` + `tools_deny=["Read"]` → 抛 `ToolFilterEmptyError`
- [x] 3.5 测 4:扩展 `tests/test_subagent_smoke.py`,加一个 `summarizer` role 端到端派发测试,验证 `tests/test_subagent_smoke.py` 里的 summarizer 派发不再失败

## 4. 验证

- [x] 4.1 跑 `pytest tests/agents/test_filter_empty_allowed.py -v` 全过
- [x] 4.2 跑 `pytest tests/test_subagent_smoke.py -v` 全过(含 summarizer 端到端)
- [x] 4.3 跑 `pytest tests/agents/ -v` 确保没有回归