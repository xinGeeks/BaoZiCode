---
name: review
description: 审查自 {since} 起的代码改动
mode: independent
allowed-tools: [Bash, Read, Grep]
history-bubbles: 3
---
独立子对话中审查自 `{since}` 起的所有代码改动,生成三段报告:
`## 摘要` / `## 风险点` / `## 建议修复`。

## SOP

1. 调 `Bash` 跑 `git log --since="{since}" --oneline` 看涉及 commit
2. 调 `Bash` 跑 `git diff <base>..HEAD --stat` 看文件级改动
3. 对每个改动文件,选 1-3 个关键 hunk 调 `Read` 看完整内容
4. 调 `Grep` 在改动文件里搜 TODO / FIXME / XXX / HACK 标记
5. 归纳 3 段:
   - **## 摘要**:改动主题 + 涉及文件 + 关键模块
   - **## 风险点**:并发 / 边界 / 错误处理 / 性能 / 安全 五个维度逐项
   - **## 建议修复**:按优先级 P0/P1/P2 排
6. 摘要回流到主对话(由 SkillLoader 负责)

## 占位符

- `{since}` — 时间/范围,如 `"5 轮前"` / `"2026-07-01"` / `"HEAD~3"`
- `{focus_area}` — 可选,聚焦审查领域(权限 / 性能 / 安全 ...)

## 注意

- 不重复 review 文件结构(只 flag 实质问题)
- 风险点要具体到行号或函数名
- P0 风险必须给出最小可行修复
- 项目可放 `.baozicode/skills/review/SKILL.md` 覆盖默认审查维度
