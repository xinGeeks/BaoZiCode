---
name: commit
description: 根据当前 git diff 生成符合 conventional commits 的 commit message
mode: shared
allowed-tools: [Bash, Read]
---
根据当前 `git diff --staged` 生成一个符合 conventional commits 规范的 commit
message,并自动 `git commit -m "<message>"`。

## SOP

1. 调 `Bash` 跑 `git diff --staged --stat` 看改动概览
2. 调 `Bash` 跑 `git diff --staged` 看完整 diff
3. 归纳改动 → 生成 commit message:
   - 格式: `<type>(<scope>): <description>`
   - type ∈ {feat, fix, refactor, docs, test, chore, perf, build, ci}
   - scope 可选,小写单词
   - description 中文,祈使句,≤ 50 字符
4. message body(可空)说明 why,每个 bullet 一行,wrap at 72
5. footer 标注关联 issue / breaking change
6. 调 `Bash` 跑 `git commit -m "<subject>" -m "<body>"`(多 -m 段)
7. 输出 commit hash 跟用户确认

## 占位符

- `{message}` — 用户在 `/commit --message="..."` 传的额外上下文(可空)

## 注意

- **不要**自动 `git push`(留给用户决定)
- **不要** amend 已 push 的 commit
- **不要** skip hooks(用 `--no-verify` 必须显式确认)
- 项目可放 `.baozicode/skills/commit/SKILL.md` 覆盖此默认行为
