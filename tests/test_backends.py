"""验证 4 个后端都满足 LLMClient 接口、都能被 factory 创建。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.config.schema import AppConfig, BackendConfig, BackendName
from baozicode.llm.anthropic import AnthropicBackend
from baozicode.llm.base import LLMClient
from baozicode.llm.deepseek import DeepSeekBackend
from baozicode.llm.factory import create_client
from baozicode.llm.minimax import MiniMaxBackend
from baozicode.llm.openai import OpenAIBackend, OpenAICompatibleBackend


def _make_config(backend: BackendName) -> AppConfig:
    return AppConfig(
        backend=backend,
        anthropic=BackendConfig(api_key="sk-a", model="claude-sonnet-4-6"),
        openai=BackendConfig(api_key="sk-o", model="gpt-5"),
        minimax=BackendConfig(api_key="sk-m", model="MiniMax-M3"),
        deepseek=BackendConfig(api_key="sk-d", model="deepseek-chat"),
    )


def test_subclass_hierarchy() -> None:
    """OpenAI/MiniMax/DeepSeek 都继承自 OpenAICompatibleBackend。"""
    for cls in (OpenAIBackend, MiniMaxBackend, DeepSeekBackend):
        assert issubclass(cls, OpenAICompatibleBackend), f"{cls.__name__} should inherit OpenAICompatibleBackend"
        assert issubclass(cls, LLMClient)
    # Anthropic 是独立的
    assert not issubclass(AnthropicBackend, OpenAICompatibleBackend)
    print("[OK] subclass hierarchy")


def test_default_attributes_differ() -> None:
    """3 个 OpenAI 兼容后端的 DEFAULT_* 必须各不相同。"""
    defaults = {
        cls.__name__: (cls.DEFAULT_BASE_URL, cls.DEFAULT_MODEL)
        for cls in (OpenAIBackend, MiniMaxBackend, DeepSeekBackend)
    }
    assert defaults["OpenAIBackend"] == ("https://api.openai.com/v1", "gpt-5")
    assert defaults["MiniMaxBackend"][0] == "https://api.minimaxi.com/v1"
    assert defaults["MiniMaxBackend"][1] == "MiniMax-M3"
    assert defaults["DeepSeekBackend"] == ("https://api.deepseek.com/v1", "deepseek-chat")
    # 三个 base_url 互不相同
    base_urls = {d[0] for d in defaults.values()}
    assert len(base_urls) == 3
    print("[OK] default base_urls and models differ across 3 OpenAI-compatible backends")


def test_constructor_uses_defaults() -> None:
    """不传 model/base_url 时使用类属性默认值。"""
    b = MiniMaxBackend(api_key="x")
    # 我们通过内部 _model 和 client 的 base_url 验证（这两个都是 _ 私有，但测试可以访问）
    assert b._model == "MiniMax-M3"
    # AsyncOpenAI 的 base_url 在底层 http_client 里，难以直接读取；
    # 改用 init 接受 None 也不报错的间接验证
    b2 = DeepSeekBackend(api_key="x")
    assert b2._model == "deepseek-chat"
    print("[OK] default model used when not provided")


def test_factory_for_all_four_backends() -> None:
    """factory 能为 4 个 backend 名称各返回正确的实例。"""
    expected = {
        "anthropic": AnthropicBackend,
        "openai": OpenAIBackend,
        "minimax": MiniMaxBackend,
        "deepseek": DeepSeekBackend,
    }
    for name, cls in expected.items():
        cfg = _make_config(name)
        client = create_client(cfg)
        assert isinstance(client, cls), f"backend={name} expected {cls.__name__} got {type(client).__name__}"
        assert isinstance(client, LLMClient)
    print("[OK] factory creates correct backend for all 4 names")


def test_config_schema_allows_all_four() -> None:
    """AppConfig 接受 4 种 backend 值。"""
    for name in ("anthropic", "openai", "minimax", "deepseek"):
        cfg = _make_config(name)
        assert cfg.backend == name
    print("[OK] AppConfig.backend accepts all 4 names")


def test_config_rejects_unknown_backend() -> None:
    """AppConfig 拒绝未知 backend 值。"""
    import pytest
    from pydantic import ValidationError
    try:
        AppConfig(
            backend="bogus",  # type: ignore[arg-type]
            anthropic=BackendConfig(api_key="x", model="m"),
            openai=BackendConfig(api_key="x", model="m"),
            minimax=BackendConfig(api_key="x", model="m"),
            deepseek=BackendConfig(api_key="x", model="m"),
        )
    except ValidationError:
        print("[OK] unknown backend value rejected")
        return
    raise AssertionError("expected ValidationError")


def test_config_requires_all_four_blocks() -> None:
    """AppConfig 缺少任一后端块时必须报错。"""
    from pydantic import ValidationError
    try:
        AppConfig(
            backend="anthropic",
            anthropic=BackendConfig(api_key="x", model="m"),
            openai=BackendConfig(api_key="x", model="m"),
            # minimax 和 deepseek 故意漏掉
        )
    except ValidationError as e:
        assert "minimax" in str(e) and "deepseek" in str(e)
        print("[OK] missing minimax/deepseek blocks caught by schema")
        return
    raise AssertionError("expected ValidationError")


# --- v0.2 tool-calling 适配 ---


def test_tool_definition_to_anthropic_format() -> None:
    """ToolDefinition.to_anthropic() 返回 SDK 期望的 dict。"""
    from baozicode.tools.base import ToolDefinition

    td = ToolDefinition(
        name="Read",
        description="Read a file",
        parameters={"type": "object", "properties": {"file_path": {"type": "string"}}},
        risk="low",
    )
    out = td.to_anthropic()
    assert out == {
        "name": "Read",
        "description": "Read a file",
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
        },
    }
    print("[OK] ToolDefinition → Anthropic format")


def test_tool_definition_to_openai_format() -> None:
    """ToolDefinition.to_openai() 返回 function-calling 格式。"""
    from baozicode.tools.base import ToolDefinition

    td = ToolDefinition(
        name="Bash",
        description="Run shell",
        parameters={"type": "object", "properties": {}},
        risk="high",
    )
    out = td.to_openai()
    assert out["type"] == "function"
    assert out["function"]["name"] == "Bash"
    assert out["function"]["parameters"] == {"type": "object", "properties": {}}
    print("[OK] ToolDefinition → OpenAI function format")


def test_anthropic_message_conversion_with_tool_blocks() -> None:
    """AnthropicBackend 把 Message(content=list) 正确转为 SDK 格式。"""
    from baozicode.llm.anthropic import _convert_messages
    from baozicode.llm.base import Message, TextBlock, ToolUseBlock, ToolResultBlock

    msgs = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content=[
                TextBlock(text="let me check"),
                ToolUseBlock(id="t1", name="Read", input={"file_path": "x"}),
            ],
        ),
        Message(
            role="tool",
            content=[ToolResultBlock(tool_use_id="t1", content="content", is_error=False)],
        ),
    ]
    out = _convert_messages(msgs)
    # user 消息直通
    assert out[0] == {"role": "user", "content": "hi"}
    # assistant 消息含 text + tool_use blocks
    assert out[1]["role"] == "assistant"
    assert out[1]["content"][0] == {"type": "text", "text": "let me check"}
    assert out[1]["content"][1]["type"] == "tool_use"
    assert out[1]["content"][1]["id"] == "t1"
    # tool 消息被翻译为 role="user" + tool_result blocks
    assert out[2]["role"] == "user"
    assert out[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": "content",
        "is_error": False,
    }
    print("[OK] Anthropic message conversion handles tool blocks")


def test_openai_message_conversion_with_tool_blocks() -> None:
    """OpenAI 把 Message(content=list) 拆分为多条消息。"""
    from baozicode.llm.openai import _convert_messages
    from baozicode.llm.base import Message, TextBlock, ToolUseBlock, ToolResultBlock

    msgs = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content=[
                TextBlock(text="reading"),
                ToolUseBlock(id="t1", name="Read", input={"file_path": "x"}),
            ],
        ),
        Message(
            role="tool",
            content=[ToolResultBlock(tool_use_id="t1", content="content", is_error=False)],
        ),
    ]
    out = _convert_messages(msgs)
    # 应该有 3 条:user, assistant(text+tool_calls), tool(tool_call_id)
    assert len(out) == 3
    assert out[0] == {"role": "user", "content": "hi"}
    assert out[1]["role"] == "assistant"
    assert out[1]["content"] == "reading"
    assert out[1]["tool_calls"][0]["function"]["name"] == "Read"
    assert out[1]["tool_calls"][0]["function"]["arguments"] == '{"file_path": "x"}'
    assert out[2] == {
        "role": "tool",
        "tool_call_id": "t1",
        "content": "content",
    }
    print("[OK] OpenAI message conversion handles tool blocks")


def test_anthropic_stream_yields_complete_tool_call_at_block_stop() -> None:
    """AnthropicBackend 在 block_stop 时 yield 完整 ToolCall,不暴露 partial JSON。"""
    import asyncio
    from unittest.mock import MagicMock

    from baozicode.llm.anthropic import AnthropicBackend
    from baozicode.llm.base import Message
    from baozicode.tools.base import ToolDefinition

    class FakeEvent:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class FakeStream:
        def __init__(self, events):
            self._events = events

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def __aiter__(self):
            return self._aiter()

        async def _aiter(self):
            for e in self._events:
                yield e

    cb = MagicMock()
    cb.type = "tool_use"
    cb.id = "t1"
    cb.name = "Read"
    cb.input = {}

    events = [
        FakeEvent(type="content_block_start", content_block=cb),
        FakeEvent(
            type="content_block_delta",
            delta=FakeEvent(type="input_json_delta", partial_json='{"file_path":'),
        ),
        FakeEvent(
            type="content_block_delta",
            delta=FakeEvent(type="input_json_delta", partial_json=' "x"}'),
        ),
        FakeEvent(type="content_block_stop"),
    ]
    fake_stream = FakeStream(events)

    fake_client = MagicMock()
    fake_client.messages.stream = MagicMock(return_value=fake_stream)

    backend = AnthropicBackend(api_key="x", model="m")
    backend._client = fake_client

    td = ToolDefinition(name="Read", description="r", parameters={})

    async def collect():
        deltas = []
        async for d in backend.stream([Message(role="user", content="hi")], tools=[td]):
            deltas.append(d)
        return deltas

    deltas = asyncio.run(collect())
    tool_deltas = [d for d in deltas if d.type == "tool_use"]
    assert len(tool_deltas) == 1, f"expected 1 tool_use delta, got {len(tool_deltas)}"
    tc = tool_deltas[0].text
    assert tc.id == "t1"
    assert tc.name == "Read"
    assert tc.arguments == {"file_path": "x"}
    assert tc.error is None
    print("[OK] Anthropic stream yields complete ToolCall at block_stop")


def test_anthropic_stream_malformed_json_yields_error_marked_toolcall() -> None:
    """input_json_delta 解析失败 → yield error-marked ToolCall,不抛异常。"""
    import asyncio
    from unittest.mock import MagicMock

    from baozicode.llm.anthropic import AnthropicBackend
    from baozicode.llm.base import Message
    from baozicode.tools.base import ToolDefinition

    class FakeEvent:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class FakeStream:
        def __init__(self, events):
            self._events = events

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def __aiter__(self):
            return self._aiter()

        async def _aiter(self):
            for e in self._events:
                yield e

    cb = MagicMock()
    cb.type = "tool_use"
    cb.id = "t1"
    cb.name = "Read"

    events = [
        FakeEvent(type="content_block_start", content_block=cb),
        FakeEvent(
            type="content_block_delta",
            delta=FakeEvent(type="input_json_delta", partial_json="{not json"),
        ),
        FakeEvent(type="content_block_stop"),
    ]
    fake_stream = FakeStream(events)

    fake_client = MagicMock()
    fake_client.messages.stream = MagicMock(return_value=fake_stream)
    backend = AnthropicBackend(api_key="x", model="m")
    backend._client = fake_client

    async def collect():
        deltas = []
        async for d in backend.stream(
            [Message(role="user", content="hi")],
            tools=[ToolDefinition(name="Read", description="r", parameters={})],
        ):
            deltas.append(d)
        return deltas

    deltas = asyncio.run(collect())
    tool_deltas = [d for d in deltas if d.type == "tool_use"]
    assert len(tool_deltas) == 1
    tc = tool_deltas[0].text
    assert tc.error is not None
    assert "failed to parse" in tc.error
    assert tc.arguments == {}
    print("[OK] Anthropic stream handles malformed JSON gracefully")


if __name__ == "__main__":
    test_subclass_hierarchy()
    test_default_attributes_differ()
    test_constructor_uses_defaults()
    test_factory_for_all_four_backends()
    test_config_schema_allows_all_four()
    test_config_rejects_unknown_backend()
    test_config_requires_all_four_blocks()
    test_tool_definition_to_anthropic_format()
    test_tool_definition_to_openai_format()
    test_anthropic_message_conversion_with_tool_blocks()
    test_openai_message_conversion_with_tool_blocks()
    test_anthropic_stream_yields_complete_tool_call_at_block_stop()
    test_anthropic_stream_malformed_json_yields_error_marked_toolcall()
    print("\nAll backend tests passed.")
