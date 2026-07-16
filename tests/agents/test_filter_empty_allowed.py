"""v1.5 ToolFilter 显式空集语义 — 边界单测。

覆盖三种情形:
1. tools=None  → filter 产出非空(L2 跳过)
2. tools=[]    → filter 产出 [] 不抛异常(L2 显式空 = 合法)
3. tools=["Read"] + tools_deny=["Read"] → filter 抛 ToolFilterEmptyError(L3 让集合变空)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from baozicode.agents.filter import GLOBAL_DENY, ToolFilter, ToolFilterEmptyError
from baozicode.agents.schema import AgentDef, AgentFrontmatter
from baozicode.tools.base import ToolDefinition
from baozicode.tools.registry import get_default_tool_registry


def _make_def(
    name: str,
    *,
    tools: list[str] | None = None,
    tools_deny: list[str] | None = None,
) -> AgentDef:
    """构造一个测试用 AgentDef(只填必要字段)。"""
    fm = AgentFrontmatter(
        name=name,
        description=f"test {name}",
        tools=tools,
        tools_deny=tools_deny,
    )
    return AgentDef(
        frontmatter=fm,
        body="",
        source="builtin",
        path=Path(f"/tmp/{name}"),
    )


@pytest.fixture
def all_tools() -> list[ToolDefinition]:
    """默认 7 工具 + task(被 L1 过滤掉)。"""
    return get_default_tool_registry().get_all_tools()


# ---------------------------------------------------------------------------
# 测 1: tools=None → L2 跳过,filter 产出非空(L1 去掉 task 后剩下的)
# ---------------------------------------------------------------------------


def test_tools_none_no_constraint(all_tools):
    """tools=None 时 L2 不收窄,产出非空(L1 GLOBAL_DENY 去掉 task)。"""
    role = _make_def("no-constraint", tools=None)
    f = ToolFilter(
        role_def=role,
        is_background=False,
        background_whitelist=[],
        all_tools=all_tools,
    )
    visible = f.visible_tools
    # L1 GLOBAL_DENY 包含 task,所以 visible 不含 task
    assert "task" not in [t.name for t in visible]
    # 其他 7 个工具应该都在
    expected = {t.name for t in all_tools} - GLOBAL_DENY
    assert {t.name for t in visible} == expected
    assert f._l2_explicit_empty is False
    assert f.layer_states["L2_explicit_empty"] is False


# ---------------------------------------------------------------------------
# 测 2: tools=[] → filter 产出 [] 不抛异常(L2 显式空 = 合法)
# ---------------------------------------------------------------------------


def test_tools_empty_list_allowed(all_tools):
    """tools=[] 时 L2 显式空,filter 返回空 list 不抛异常。"""
    role = _make_def("empty-list", tools=[])
    f = ToolFilter(
        role_def=role,
        is_background=False,
        background_whitelist=[],
        all_tools=all_tools,
    )
    visible = f.visible_tools
    assert visible == [], f"期望空 list,得到 {visible}"
    assert f._l2_explicit_empty is True
    assert f.layer_states["L2_explicit_empty"] is True


def test_tools_empty_distinguishes_from_none(all_tools):
    """显式 [] 和 None 在 layer_states 里要能区分。"""
    f_empty = ToolFilter(
        role_def=_make_def("a-empty", tools=[]),
        is_background=False,
        background_whitelist=[],
        all_tools=all_tools,
    )
    f_none = ToolFilter(
        role_def=_make_def("b-none", tools=None),
        is_background=False,
        background_whitelist=[],
        all_tools=all_tools,
    )
    # visible_tools 触发 _l2_explicit_empty 计算
    f_empty.visible_tools
    f_none.visible_tools
    assert f_empty._l2_explicit_empty is True
    assert f_none._l2_explicit_empty is False


# ---------------------------------------------------------------------------
# 测 3: tools=["Read"] + tools_deny=["Read"] → ToolFilterEmptyError
# ---------------------------------------------------------------------------


def test_tools_deny_makes_empty_raise(all_tools):
    """L3 让集合变空仍报错(配置冲突,不是显式声明)。"""
    role = _make_def("conflict", tools=["Read"], tools_deny=["Read"])
    f = ToolFilter(
        role_def=role,
        is_background=False,
        background_whitelist=[],
        all_tools=all_tools,
    )
    with pytest.raises(ToolFilterEmptyError) as exc_info:
        _ = f.visible_tools
    # error 应带 layer_states 诊断信息
    assert "L2_explicit_empty" in str(exc_info.value)
    assert exc_info.value.layer_states["L2_explicit_empty"] is False


# ---------------------------------------------------------------------------
# 测 4: role=None(fork 模式)+ 全空 list → fork 不走 L2,应产出非空
# ---------------------------------------------------------------------------


def test_role_none_fork_ignores_explicit_empty(all_tools):
    """fork 模式(role=None)不参与 L2,空集不影响。"""
    f = ToolFilter(
        role_def=None,
        is_background=False,
        background_whitelist=[],
        all_tools=all_tools,
    )
    visible = f.visible_tools
    # fork 模式 L2 跳过,但 L1 GLOBAL_DENY 仍然去掉 task
    assert "task" not in [t.name for t in visible]
    assert len(visible) > 0
    assert f._l2_explicit_empty is False


# ---------------------------------------------------------------------------
# 测 5: empty allowlist 在 background + 非 whitelist 情形下也走 L2
#        (background=True 时 L4 收窄;但 L2 显式空应优先)
# ---------------------------------------------------------------------------


def test_explicit_empty_overrides_background(all_tools):
    """L2 显式空在 background=True 时也应放行(因为 final 空 = 显式空路径优先)。"""
    role = _make_def("empty-bg", tools=[])
    f = ToolFilter(
        role_def=role,
        is_background=True,  # background 模式 L4 也收窄
        background_whitelist=["Read"],  # 只允许 Read,但 L2 已经空
        all_tools=all_tools,
    )
    visible = f.visible_tools
    # 显式空优先,即使 background + L4 收窄也返回 []
    assert visible == []
    assert f._l2_explicit_empty is True