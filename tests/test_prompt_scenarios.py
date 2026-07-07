"""v0.4 Phase 3 — prompt scenario integration tests.

3 scenario tests:
1. Grep-not-Bash rule — Grep gets the prefer_specialized_tools prefix; Bash does NOT.
2. unknown_tool guard works — plan_mode + Bash tool_call → UNKNOWN_TOOL_HALLUCINATION.
3. plan_mode locks writes — augmented_tools excludes Write/Edit/Bash, includes Read/Grep/Glob/WebFetch.
"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.agent.events import AgentEvent, StopReason
from baozicode.agent.loop import Agent
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.prompt.rules import RuleRegistry
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import get_all_tools

sys.path.insert(0, str(Path(__file__).parent))
from _agent_helpers import make_minimal_config


# ---- helpers ----


class _Perm:
    def __init__(
        self,
        deny: list[str] | None = None,
        auto_allow: list[str] | None = None,
        batch_confirm: bool = False,
        bash_locked_cwd: bool = False,
    ) -> None:
        self.deny = deny if deny is not None else []
        self.auto_allow = auto_allow if auto_allow is not None else []
        self.batch_confirm = batch_confirm
        self.bash_locked_cwd = bash_locked_cwd


async def _drain(agent: Agent, text: str) -> list[AgentEvent]:
    out: list[AgentEvent] = []
    async for ev in agent.run(text):
        out.append(ev)
    return out


# ---- 1: Grep-not-Bash rule ----


def test_grep_rule_prefers_specialized_tool_over_bash() -> None:
    """RuleRegistry.augment_tool(Grep) injects a 'prefer this specialized tool' hint;
    augment_tool(Bash) does NOT contain that hint (it only carries its own bash_timeout hint).
    """
    registry = RuleRegistry()
    tools = {t.name: t for t in get_all_tools()}
    assert "Grep" in tools and "Bash" in tools, "test fixture must have Grep and Bash tools"

    aug_grep = registry.augment_tool(tools["Grep"])
    aug_bash = registry.augment_tool(tools["Bash"])

    # Grep is in the prefer_specialized_tools rule's applies_to → should have its tool_prefix
    # The rule's tool_prefix starts with 【优先】(see baozicode/prompt/rules.py).
    assert "【优先】" in aug_grep.description, (
        f"Grep description should contain the 【优先】 prefix from prefer_specialized_tools; "
        f"got: {aug_grep.description[:120]!r}"
    )
    # Bash is NOT in prefer_specialized_tools → it should NOT have 【优先】 in its description
    assert "【优先】" not in aug_bash.description, (
        f"Bash description should NOT contain the 【优先】 prefix; got: {aug_bash.description[:120]!r}"
    )
    print("[OK] Grep-not-Bash: Grep gets 【优先】 prefix; Bash does not")


# ---- 2: unknown_tool guard in plan mode ----


class _ScriptedLLM(LLMClient):
    """Plan-mode resisting LLM: emits a Bash tool call twice to trigger UNKNOWN_TOOL_HALLUCINATION."""

    def __init__(self) -> None:
        self.call_count = 0

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        # Bash is unknown under plan_mode because the filtered tool list excludes it.
        call = ToolCall(id=f"x{self.call_count}", name="Bash", arguments={"command": "ls"})
        yield ContentDelta(type="text", text="hmm")
        yield ContentDelta(type="tool_use", text=call)


async def test_unknown_tool_guard_triggers_under_plan_mode() -> None:
    """plan_mode=True + LLM emits Bash → Agent should terminate with UNKNOWN_TOOL_HALLUCINATION
    because Bash is not in the filtered available_tools list.
    """
    conv = ConversationManager()
    a = Agent(
        llm_client=_ScriptedLLM(),
        tools=get_all_tools(),
        conversation=conv,
        permissions=_Perm(),
        config=make_minimal_config(),
        plan_mode=True,
    )
    events = await _drain(a, "list files")
    reasons = [ev.payload for ev in events if ev.type == "done"]
    assert reasons, "Agent should have emitted a done event"
    assert reasons[-1] == StopReason.UNKNOWN_TOOL_HALLUCINATION, (
        f"expected UNKNOWN_TOOL_HALLUCINATION, got {reasons[-1]}"
    )
    print("[OK] plan_mode + Bash tool call → UNKNOWN_TOOL_HALLUCINATION termination")


# ---- 3: plan_mode locks writes ----


def test_plan_mode_locks_writes() -> None:
    """Agent(plan_mode=True)._prompt.augmented_tools excludes Write/Edit/Bash;
    includes Read/Grep/Glob/WebFetch.
    """
    llm = _ScriptedLLM.__new__(_ScriptedLLM)  # skip __init__
    a = Agent(
        llm_client=llm,
        tools=get_all_tools(),
        conversation=ConversationManager(),
        permissions=_Perm(),
        config=make_minimal_config(),
        plan_mode=True,
    )
    names = {t.name for t in a._prompt.augmented_tools}
    # Side-effect tools must NOT be present
    for blocked in ("Write", "Edit", "Bash"):
        assert blocked not in names, (
            f"plan_mode=True should exclude {blocked} from augmented_tools; got {names}"
        )
    # Read-only tools must be present
    for allowed in ("Read", "Grep", "Glob", "WebFetch"):
        assert allowed in names, (
            f"plan_mode=True should include {allowed} in augmented_tools; got {names}"
        )
    print(f"[OK] plan_mode locks writes: augmented_tools = {sorted(names)}")


def main() -> None:
    test_grep_rule_prefers_specialized_tool_over_bash()
    asyncio.run(test_unknown_tool_guard_triggers_under_plan_mode())
    test_plan_mode_locks_writes()
    print("\nAll prompt_scenarios tests passed.")


if __name__ == "__main__":
    main()