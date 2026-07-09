# v1.1 — Hooks 生命周期 设计演进记录

这 6 张截图保留了 v1.1 从初步需求到最终设计的演进过程,方便回顾
「为什么这样定」。

| 文件 | 内容 |
|---|---|
| `1.png` | 用户初始需求:在 Agent 关键节点挂「事件 + 条件 + 动作」三要素规则 |
| `2.png` | 设计讨论 #1 —— 4 条件种类(exact / glob / regex / not_)与 4 种 action 的取舍 |
| `3.png` | 设计讨论 #2 —— 流水线形态(L1 → hook.pre → L2-L5 → execute → hook.post)+ 失败策略 + ToolResult 字段扩展 |
| `4.png` | 配置讨论:hook 规则嵌 `config.yaml` 的 `hooks:` 块(不单独搞 `hooks.yaml`) |
| `5.png` | 条件语法决定:复用权限规则的 `fnmatch` glob + 加 `not_` 反向前缀,不全换 DSL |
| `6.png` | YAML schema 形态示例 + Bash 工具的几种典型 hook 规则 |

最终落地的设计在 `openspec/changes/archive/2026-07-09-v1-1-hooks-lifecycle/`
完整存档(提案 + 设计 + 任务 + 5 个 spec)。这套截图作为「从需求到定稿」
的中间态留档,方便未来 v1.1.1 / v1.2 修订时回看当时为什么这么选。

无关代码 — 仅作历史参考。
