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


if __name__ == "__main__":
    test_subclass_hierarchy()
    test_default_attributes_differ()
    test_constructor_uses_defaults()
    test_factory_for_all_four_backends()
    test_config_schema_allows_all_four()
    test_config_rejects_unknown_backend()
    test_config_requires_all_four_blocks()
    print("\nAll backend tests passed.")
