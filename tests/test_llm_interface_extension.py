"""v0.4 Phase 3 — LLMClient.stream interface extension tests.

2 tests:
1. cache_breakpoints is keyword-only on LLMClient.stream
2. All 4 backends (Anthropic, OpenAI, MiniMax, DeepSeek) accept cache_breakpoints kwarg
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.llm.anthropic import AnthropicBackend
from baozicode.llm.base import LLMClient
from baozicode.llm.deepseek import DeepSeekBackend
from baozicode.llm.minimax import MiniMaxBackend
from baozicode.llm.openai import OpenAIBackend, OpenAICompatibleBackend


# ---- 1: cache_breakpoints is keyword-only ----


def test_cache_breakpoints_is_keyword_only() -> None:
    """LLMClient.stream must declare cache_breakpoints as KEYWORD_ONLY."""
    sig = inspect.signature(LLMClient.stream)
    assert "cache_breakpoints" in sig.parameters, "cache_breakpoints param missing from LLMClient.stream"
    param = sig.parameters["cache_breakpoints"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        f"cache_breakpoints should be KEYWORD_ONLY, got {param.kind}"
    )
    print("[OK] LLMClient.stream.cache_breakpoints is KEYWORD_ONLY")


# ---- 2: All 4 backends accept cache_breakpoints kwarg ----


def _has_cache_breakpoints(cls) -> bool:
    """Return True iff cls.stream has cache_breakpoints in its signature."""
    sig = inspect.signature(cls.stream)
    return "cache_breakpoints" in sig.parameters


def test_all_four_backends_accept_cache_breakpoints() -> None:
    """All 4 concrete backends must declare cache_breakpoints in their stream() signature."""
    backends = [
        ("AnthropicBackend", AnthropicBackend),
        ("OpenAIBackend", OpenAIBackend),
        ("MiniMaxBackend", MiniMaxBackend),
        ("DeepSeekBackend", DeepSeekBackend),
    ]
    missing = []
    for name, cls in backends:
        if not _has_cache_breakpoints(cls):
            missing.append(name)
    assert not missing, f"these backends lack cache_breakpoints in stream(): {missing}"
    # Also verify the shared parent (OpenAICompatibleBackend) declares it so
    # the inherited subclasses (MiniMax, DeepSeek, OpenAI) satisfy the contract.
    assert _has_cache_breakpoints(OpenAICompatibleBackend), (
        "OpenAICompatibleBackend.stream must declare cache_breakpoints so subclasses inherit it"
    )
    print("[OK] Anthropic/OpenAI/MiniMax/DeepSeek all accept cache_breakpoints kwarg")


def main() -> None:
    test_cache_breakpoints_is_keyword_only()
    test_all_four_backends_accept_cache_breakpoints()
    print("\nAll llm_interface_extension tests passed.")


if __name__ == "__main__":
    main()