---
name: test
description: 跑 pytest 收集失败用例并分析根因
mode: independent
allowed-tools: [Bash]
history-bubbles: 2
---
独立子对话中跑项目测试套件,收集失败的 case,分析根因,
生成报告回流到主对话。

## SOP

1. 调 `Bash` 跑 `pytest --tb=short -q 2>&1 | tail -100` 收集结果
2. 如果 timeout 或 hang:用 `pytest --timeout=60 -x` 重跑快速定位
3. 解析失败 case 的 traceback:
   - 哪个测试 / 哪个文件 / 哪一行
   - AssertionError / Exception 类型
   - 失败值 vs 期望值
4. 归纳为 3 段:
   - **## 概览**:总测试数 / 通过 / 失败 / 跳过 / 耗时
   - **## 失败明细**:每个失败 case 一段,含 traceback 关键 5 行
   - **## 根因分析**:失败的代码/逻辑/数据问题分类
5. 摘要回流到主对话

## 占位符

- `{path}` — 可选,指定测试文件/目录(如 `tests/test_foo.py` 或 `tests/skills/`)
- `{marker}` — 可选,只跑特定 marker 的测试(如 `-m "not slow"`)

## 注意

- **不要** 自动 fix 测试(只报告)
- 失败 ≥ 10 个时只列 top 10 + "还有 N 个类似失败"
- 项目可放 `.baozicode/skills/test/SKILL.md` 覆盖默认 runner
- 如果项目用别的测试框架(unittest / nose),改 allowed-tools 加 Read +
  Grep + 改 SOP 描述
