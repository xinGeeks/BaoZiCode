"""v0.8 Phase 9.4:memory_path 启动 warning 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_memory_path_default_no_warning(tmp_path: Path, capsys) -> None:
    """默认 memory_path(用户没改)→ 不打 WARN。"""
    from baozicode.config.loader import load_config

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "backend: anthropic\n"
        "anthropic:\n  api_key: k\n  model: m\n"
        "openai:\n  api_key: k\n  model: m\n"
        "minimax:\n  api_key: k\n  model: m\n"
        "deepseek:\n  api_key: k\n  model: m\n",
        encoding="utf-8",
    )
    load_config(str(cfg_path))
    captured = capsys.readouterr()
    assert "WARN: memory_path is deprecated" not in captured.err
    print("[OK] 默认 memory_path → 不打 WARN")


def test_memory_path_custom_existing_warns(tmp_path: Path, capsys) -> None:
    """用户显式覆盖 memory_path + 文件存在 → 打 WARN。"""
    from baozicode.config.loader import load_config

    legacy = tmp_path / "my_legacy.md"
    legacy.write_text("# 旧记忆\n", encoding="utf-8")

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"backend: anthropic\n"
        f"anthropic:\n  api_key: k\n  model: m\n"
        f"openai:\n  api_key: k\n  model: m\n"
        f"minimax:\n  api_key: k\n  model: m\n"
        f"deepseek:\n  api_key: k\n  model: m\n"
        f"memory_path: {legacy}\n",
        encoding="utf-8",
    )
    load_config(str(cfg_path))
    captured = capsys.readouterr()
    assert "WARN: memory_path is deprecated" in captured.err
    assert "MEMORY.md" in captured.err
    print("[OK] 自定义 memory_path + 文件存在 → 打 WARN")


def test_memory_path_custom_but_missing_no_warning(tmp_path: Path, capsys) -> None:
    """用户显式覆盖 memory_path 但文件不存在 → 不打 WARN(避免噪音)。"""
    from baozicode.config.loader import load_config

    nonexistent = tmp_path / "never_created.md"

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"backend: anthropic\n"
        f"anthropic:\n  api_key: k\n  model: m\n"
        f"openai:\n  api_key: k\n  model: m\n"
        f"minimax:\n  api_key: k\n  model: m\n"
        f"deepseek:\n  api_key: k\n  model: m\n"
        f"memory_path: {nonexistent}\n",
        encoding="utf-8",
    )
    load_config(str(cfg_path))
    captured = capsys.readouterr()
    assert "WARN: memory_path is deprecated" not in captured.err
    print("[OK] 自定义 memory_path 但文件不存在 → 不打 WARN")


def test_memory_path_field_has_deprecation_description() -> None:
    """schema 上 memory_path field 有 deprecation 描述。"""
    from baozicode.config.schema import AppConfig

    field_info = AppConfig.model_fields["memory_path"]
    desc = field_info.description or ""
    assert "Deprecated" in desc
    assert "v0.8" in desc
    assert "v0.9" in desc
    print(f"[OK] schema memory_path description: {desc!r}")


def test_memory_config_in_app_config(tmp_path: Path) -> None:
    """AppConfig 默认带 memory: MemoryConfig(enabled=True)。"""
    from baozicode.config.schema import AppConfig, BackendConfig

    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="k", model="m"),
        openai=BackendConfig(api_key="k", model="m"),
        minimax=BackendConfig(api_key="k", model="m"),
        deepseek=BackendConfig(api_key="k", model="m"),
    )
    assert cfg.memory.enabled is True
    assert cfg.memory.index_max_lines == 200
    assert cfg.memory.index_max_bytes == 25_600
    assert cfg.sessions.enabled is True
    assert cfg.sessions.retention_days == 30
    print("[OK] AppConfig 默认带 memory + sessions 配置块")
