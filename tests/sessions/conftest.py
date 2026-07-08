"""v0.8 sessions tests — shared fixtures."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.config.schema import CompactionConfig
from baozicode.context import CompactionTelemetry, ContextConfig, ContextStorage, MaybeCompactContext
from baozicode.llm.base import ContentDelta, LLMClient
from baozicode.sessions.archive import SessionArchiver


@pytest.fixture
def tmp_project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def tmp_sessions_root(tmp_project_root: Path) -> Path:
    return tmp_project_root / ".baozicode" / "sessions"


@pytest.fixture
def archiver(tmp_sessions_root: Path) -> SessionArchiver:
    return SessionArchiver(tmp_sessions_root, session_id="20260708-153000-a1b2")


@pytest.fixture
def context_storage(tmp_project_root: Path) -> ContextStorage:
    return ContextStorage(
        project_root=tmp_project_root, session_id="20260708-153000-a1b2"
    )


class _NoopLLM(LLMClient):
    """测试用 LLM — 不发任何 token,resume 触发 maybe_compact 时不会真的 Layer 2。"""

    async def stream(
        self,
        messages,
        system=None,
        tools=None,
        *,
        cache_breakpoints=None,
    ) -> AsyncIterator[ContentDelta]:
        # 立即 stop — Layer 2 在没 token 时不触发,只 Layer 1
        if False:
            yield ContentDelta(type="text", text="")  # pragma: no cover


@pytest.fixture
def mock_llm() -> _NoopLLM:
    return _NoopLLM()


@pytest.fixture
def compact_ctx(
    context_storage: ContextStorage, mock_llm: _NoopLLM
) -> MaybeCompactContext:
    config = ContextConfig.build(
        context_window_tokens=128_000,
        trigger="resume",
        compaction=CompactionConfig(),
    )
    return MaybeCompactContext(
        llm=mock_llm,
        storage=context_storage,
        config=config,
        telemetry=CompactionTelemetry(),
    )