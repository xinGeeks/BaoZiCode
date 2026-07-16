## Context

`baozicode/agents/filter.py` 当前对所有 sub-Agent 工具过滤走 4 层 AND(L1 GLOBAL_DENY → L2 role.tools 白名单 → L3 role.tools_deny 黑名单 → L4 background whitelist),任何一层让最终集合变空就抛 `ToolFilterEmptyError`。

`AgentFrontmatter.tools: list[str] | None = None`(Pydantic 字段)允许 `[]`(空列表)与 `None` 区分。YAML 里 `tools: []` 解析为 `[]`,缺省 / `tools: null` 解析为 `None`。当前 filter 实现把这两种情况都当成"无约束":

```python
if role is not None and role.tools is not None:
    allow = set(role.tools)
    l2_after = [t for t in l1_after if t.name in allow]
else:
    l2_after = l1_after
```

所以 `tools: []` 在当前实现下被 L2 解释为 `allow = set([])` → `l2_after = []`,然后被空集检查捕获。

## Goals / Non-Goals

**Goals:**
- 让 `tools: []` 成为一等公民语义:"我要空工具集"是合法声明,filter 不报错
- 保持 `tools: None` 语义不变(无约束 = 全放行)
- 保持 `tools_deny` 让集合变空仍报错(配错时早 fail)
- builtin `summarizer` 从不可用变可用

**Non-Goals:**
- 不改 L1/L4 的语义(GLOBAL_DENY 和 background whitelist 仍是隐式约束,产出空就报错)
- 不改 AgentFrontmatter 的 schema(Pydantic 已支持 `[]` vs `None`)
- 不改 summarizer 的 max_iterations / model / permission_mode

## Decisions

### D1: 在 filter.py 里加 explicit-empty 分支

修改 `ToolFilter.visible_tools` cached_property:

```python
# L2: role.tools(白名单)
#   - None = 无约束(全放行)
#   - []   = 显式空(角色主动声明"我要空工具集",允许)
#   - [..] = 白名单
if role is not None and role.tools is not None:
    allow = set(role.tools)
    explicit_empty = (allow == set())  # 显式 []
    l2_after = [t for t in l1_after if t.name in allow]
else:
    l2_after = l1_after
    explicit_empty = False  # None 不算 explicit
```

然后改最后空集检查:

```python
if not final:
    # explicit_empty 路径:role.tools=[] 是合法声明,放行
    if explicit_empty:
        return []  # 显式空工具集
    # 否则是配置冲突
    raise ToolFilterEmptyError(...)
```

**Rationale**: 只在 L2 显式空时放行。L3 `tools_deny` 让集合变空仍是配置冲突(role 想 deny 某个工具,但它在 L2 允许集里),不让静默通过以免掩盖真实错误。

### D2: summarizer frontmatter 改写

```yaml
# 旧
tools-deny: [Write, Edit, Bash, Read, Grep, Glob, WebFetch]

# 新
tools: []
```

body 补一句:

> 这是"无工具"角色 — `tools: []` 是显式声明,filter 接受空工具集。
> 纯 LLM 调用,适合短文本摘要 / 文本压缩。

**Rationale**: `tools: []` 比 `tools-deny=[ALL]` 更准确表达"我要空工具集"语义,且对未来加新内置工具时不需要维护 deny 列表(deny 列表会漏)。

### D3: 单测覆盖三种情形

`tests/agents/test_filter_empty_allowed.py`:

1. `tools=None` + 默认 all_tools:filter 产出非空(全放行)
2. `tools=[]` + 默认 all_tools:filter 产出 `[]`,不抛异常
3. `tools=["Read"]` + `tools_deny=["Read"]`:filter 抛 `ToolFilterEmptyError`(L3 让集合变空 = 配置冲突)

## Risks / Trade-offs

- [Risk] `tools: []` 跟"未配置 tools"的 YAML 写法(`tools:` 单独一行无值)在某些解析器下都解析为 `None` 而不是 `[]` → **Mitigation**: 单测里直接构造 `AgentFrontmatter(tools=[])` 验证 Pydantic 行为;在 frontmatter 文档里明确写 `tools: []` 不能省
- [Risk] LLM 误派发给 summarizer 后跑出来"无工具可用" → **Mitigation**: summarizer body 已经是「无工具」语义,LLM 不会尝试调用工具;filter 行为只是让派发成功,不影响运行
- [Risk] 漏改其他 builtin 角色的 deny 列表 → **Mitigation**: 搜所有 builtin 角色 frontmatter,确认没有其他角色声明 `tools-deny=[ALL]`

## Migration Plan

无。filter 行为向后兼容:已有角色的 `tools`/`tools_deny` 行为完全不变,只是新增"显式空 = OK"这一种新语义。summarizer 从不可用变可用,但调用它的代码路径本来就在 catch 错误,不会崩。

## Open Questions

(无 — 决策已锁)