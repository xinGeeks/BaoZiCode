"""不依赖真实 API Key 的冒烟测试。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from baozicode.config.schema import AppConfig, BackendConfig
from baozicode.config.loader import load_config, ConfigError
from baozicode.llm.factory import create_client
from baozicode.llm.base import LLMClient, Message
from baozicode.conversation.manager import ConversationManager


def test_config_schema() -> None:
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="sk-test", model="claude-sonnet-4-6"),
        openai=BackendConfig(api_key="sk-test", model="gpt-5"),
        minimax=BackendConfig(api_key="sk-test", model="MiniMax-M3"),
        deepseek=BackendConfig(api_key="sk-test", model="deepseek-chat"),
    )
    assert cfg.active().api_key == "sk-test"
    assert cfg.active().model == "claude-sonnet-4-6"
    print("[OK] AppConfig schema (4 backends)")


def test_factory() -> None:
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="sk-test-a", model="claude-sonnet-4-6"),
        openai=BackendConfig(api_key="sk-test-o", model="gpt-5"),
        minimax=BackendConfig(api_key="sk-test-m", model="MiniMax-M3"),
        deepseek=BackendConfig(api_key="sk-test-d", model="deepseek-chat"),
    )
    client = create_client(cfg)
    assert isinstance(client, LLMClient)
    print(f"[OK] factory -> {type(client).__name__} for backend={cfg.backend}")

    # 验证能切到任意 backend
    for target in ("openai", "minimax", "deepseek"):
        from baozicode.config.schema import AppConfig as _AC
        cfg2 = cfg.model_copy(update={"backend": target})  # type: ignore[arg-type]
        c = create_client(cfg2)
        assert isinstance(c, LLMClient)
    print("[OK] factory works for all 4 backends")


def test_conversation() -> None:
    conv = ConversationManager()
    conv.add_user("hello")
    conv.add_assistant("hi there")
    conv.add_user("how are you?")
    msgs = conv.to_list()
    assert len(msgs) == 3
    assert msgs[0].role == "user"
    assert msgs[0].content == "hello"
    assert msgs[1].role == "assistant"
    assert msgs[2].content == "how are you?"
    conv.clear()
    assert len(conv.to_list()) == 0
    print("[OK] ConversationManager add/clear/order")


def test_config_missing() -> None:
    try:
        load_config(explicit_path="/nonexistent/xxx.yaml")
    except ConfigError as e:
        print(f"[OK] missing config raises ConfigError: {str(e)[:60]}...")
        return
    raise AssertionError("expected ConfigError")


def test_placeholder_missing(monkeypatch=None) -> None:
    import os
    os.environ.pop("BAOZI_TEST_VAR_XYZ", None)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(
            "backend: anthropic\n"
            "anthropic:\n  api_key: ${BAOZI_TEST_VAR_XYZ}\n  model: m\n"
            "openai:\n  api_key: x\n  model: m\n"
            "minimax:\n  api_key: x\n  model: m\n"
            "deepseek:\n  api_key: x\n  model: m\n"
        )
        path = f.name
    try:
        load_config(explicit_path=path)
    except ConfigError as e:
        print(f"[OK] missing env var: {str(e)[:60]}...")
        return
    finally:
        Path(path).unlink()
    raise AssertionError("expected ConfigError")


if __name__ == "__main__":
    test_config_schema()
    test_factory()
    test_conversation()
    test_config_missing()
    test_placeholder_missing()
    print("\nAll smoke tests passed.")
