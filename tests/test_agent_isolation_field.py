"""v1.3 Worktree Isolation — `AgentFrontmatter.isolation` 字段测试。

覆盖 `openspec/changes/v1-3-worktree-isolation/specs/agent-runtime/
spec.md` 中关于 isolation field 的 acceptance scenario。
"""

from __future__ import annotations

import pytest

from baozicode.agents.schema import parse_agent


# ---------------------------------------------------------------------------
# isolation 字段定义 + 默认值
# ---------------------------------------------------------------------------


class TestIsolationField:
    """`isolation` 字段在 Pydantic model 上的行为。"""

    def test_default_none_means_no_isolation(self) -> None:
        """AGENT.md 没写 `isolation` 字段 → 默认 None,行为等价 v1.2。"""
        md = (
            "---\n"
            "name: explorer\n"
            "description: reads stuff\n"
            "---\n"
            "body\n"
        )
        fm, _ = parse_agent(md)
        assert fm.isolation is None

    def test_explicit_worktree_accepted(self) -> None:
        """合法值 `"worktree"` → 接受。"""
        md = (
            "---\n"
            "name: api-designer\n"
            "description: builds api\n"
            "isolation: worktree\n"
            "---\n"
            "body\n"
        )
        fm, _ = parse_agent(md)
        assert fm.isolation == "worktree"

    def test_pydantic_rejects_bad_value(self) -> None:
        """拼错如 `Worktree`(大写) → Pydantic 报错。"""
        md = (
            "---\n"
            "name: api-designer\n"
            "description: builds api\n"
            "isolation: Worktree\n"
            "---\n"
            "body\n"
        )
        with pytest.raises(ValueError) as exc_info:
            parse_agent(md)
        # Pydantic 校验错;消息含字段名 + 不允许的值
        msg = str(exc_info.value)
        assert "isolation" in msg.lower() or "worktree" in msg.lower()

    def test_pydantic_rejects_unknown_value(self) -> None:
        """未列入 Literal 的值(`docker`、`none`、`worktree-please`) → 报。"""
        for bad in ("docker", "none", "worktree-please", "Worktree", "WORKTREE"):
            md = (
                "---\n"
                "name: api-designer\n"
                "description: builds api\n"
                f"isolation: {bad}\n"
                "---\n"
                "body\n"
            )
            with pytest.raises(ValueError):
                parse_agent(md)

    def test_isolation_none_accepted_explicitly(self) -> None:
        """显式写 `isolation: null` 或 `isolation:` 空值 → None。"""
        md = (
            "---\n"
            "name: x\n"
            "description: y\n"
            "isolation:\n"
            "---\n"
            "body\n"
        )
        fm, _ = parse_agent(md)
        assert fm.isolation is None

    def test_isolation_does_not_break_other_fields(self) -> None:
        """isolation 不影响其他字段的解析。"""
        md = (
            "---\n"
            "name: api-designer\n"
            "description: builds api\n"
            "model: sonnet\n"
            "max-iterations: 30\n"
            "permission-mode: strict\n"
            "nesting-depth: 1\n"
            "isolation: worktree\n"
            "---\n"
            "body\n"
        )
        fm, _ = parse_agent(md)
        assert fm.isolation == "worktree"
        assert fm.model == "sonnet"
        assert fm.max_iterations == 30
        assert fm.permission_mode == "strict"
        assert fm.nesting_depth == 1


# ---------------------------------------------------------------------------
# builtin agents 不自动开 isolation(v1.2 兼容)
# ---------------------------------------------------------------------------


class TestBuiltinAgentsUnchanged:
    """builtin agents 不加 isolation 字段 → v1.2 行为 0 变化。"""

    def test_explorer_no_isolation_field(self) -> None:
        """`builtin/explorer/AGENT.md` 没 isolation → None。"""
        from pathlib import Path
        builtin = (
            Path(__file__).parent.parent
            / "baozicode" / "agents" / "builtin" / "explorer" / "AGENT.md"
        )
        if not builtin.exists():
            pytest.skip(f"builtin/explorer/AGENT.md 不存在: {builtin}")
        md = builtin.read_text(encoding="utf-8")
        fm, _ = parse_agent(md)
        assert fm.isolation is None, (
            f"explorer 不应自动开 isolation,得到 {fm.isolation!r}"
        )

    def test_summarizer_no_isolation_field(self) -> None:
        """`builtin/summarizer/AGENT.md` 没 isolation → None。"""
        from pathlib import Path
        builtin = (
            Path(__file__).parent.parent
            / "baozicode" / "agents" / "builtin" / "summarizer" / "AGENT.md"
        )
        if not builtin.exists():
            pytest.skip(f"builtin/summarizer/AGENT.md 不存在: {builtin}")
        md = builtin.read_text(encoding="utf-8")
        fm, _ = parse_agent(md)
        assert fm.isolation is None, (
            f"summarizer 不应自动开 isolation,得到 {fm.isolation!r}"
        )