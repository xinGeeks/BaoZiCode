"""MCP server 配置 schema + loader 测试。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pydantic
import pytest
import yaml

from baozicode.config.loader import load_config
from baozicode.config.schema import (
    McpServerHttpConfig,
    McpServerStdioConfig,
)


def _make_full_config(mcp_servers: dict | None = None) -> dict:
    """构造一个 AppConfig 必填字段齐全的最小 dict。"""
    return {
        "backend": "anthropic",
        "anthropic": {"api_key": "k", "model": "m"},
        "openai": {"api_key": "k", "model": "m"},
        "minimax": {"api_key": "k", "model": "m"},
        "deepseek": {"api_key": "k", "model": "m"},
        "mcp_servers": mcp_servers or {},
    }


class TestStdioVariant:
    def test_minimal_command_only(self) -> None:
        cfg = McpServerStdioConfig(command="uvx")
        assert cfg.type == "stdio"
        assert cfg.command == "uvx"
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.cwd is None
        assert cfg.init_timeout_s == 5.0
        assert cfg.call_timeout_s == 60.0

    def test_full_fields(self) -> None:
        cfg = McpServerStdioConfig(
            command="uvx",
            args=["--from", "anthropic-mcp-fs", "server"],
            env={"FOO": "bar", "PATH": "/x"},
            cwd="/tmp",
        )
        assert cfg.args == ["--from", "anthropic-mcp-fs", "server"]
        assert cfg.env == {"FOO": "bar", "PATH": "/x"}
        assert cfg.cwd == "/tmp"

    def test_extra_fields_ignored(self) -> None:
        cfg = McpServerStdioConfig(command="x", unknown_field=42)  # type: ignore[call-arg]
        assert not hasattr(cfg, "unknown_field")

    def test_missing_command_raises(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            McpServerStdioConfig()


class TestHttpVariant:
    def test_minimal_url_only(self) -> None:
        cfg = McpServerHttpConfig(url="http://localhost:8000/mcp")
        assert cfg.type == "http"
        assert cfg.url == "http://localhost:8000/mcp"
        assert cfg.headers == {}
        assert cfg.init_timeout_s == 5.0

    def test_with_headers(self) -> None:
        cfg = McpServerHttpConfig(
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer x", "X-Custom": "y"},
        )
        assert cfg.headers == {"Authorization": "Bearer x", "X-Custom": "y"}

    def test_missing_url_raises(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            McpServerHttpConfig()


class TestDiscriminatedUnion:
    def test_resolves_stdio_by_type(self) -> None:
        cfg = McpServerStdioConfig.model_validate({"type": "stdio", "command": "x"})
        assert isinstance(cfg, McpServerStdioConfig)

    def test_resolves_http_by_type(self) -> None:
        cfg = McpServerHttpConfig.model_validate({"type": "http", "url": "http://x/y"})
        assert isinstance(cfg, McpServerHttpConfig)


class TestEnvSubstitution:
    def test_command_env_var_expansion(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("MCP_PKG", "anthropic-mcp-fs")
        cfg_dict = _make_full_config(
            {"fs": {"type": "stdio", "command": "uvx", "args": ["--from", "${MCP_PKG}"]}}
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=tmp_path
        ) as f:
            yaml.dump(cfg_dict, f)
            path = f.name
        try:
            config = load_config(path)
            assert config.mcp_servers["fs"].args == ["--from", "anthropic-mcp-fs"]
        finally:
            os.unlink(path)

    def test_http_headers_env_var_expansion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MCP_TOKEN", "secret-123")
        cfg_dict = _make_full_config(
            {
                "gh": {
                    "type": "http",
                    "url": "https://api.example.com/mcp",
                    "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
                }
            }
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=tmp_path
        ) as f:
            yaml.dump(cfg_dict, f)
            path = f.name
        try:
            config = load_config(path)
            assert config.mcp_servers["gh"].headers == {
                "Authorization": "Bearer secret-123"
            }
        finally:
            os.unlink(path)


class TestTwoLayerMerge:
    def test_project_overrides_user_per_server(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """项目级 mcp_servers.foo 与用户级同 key → 项目级胜。"""
        user_cfg = _make_full_config({"foo": {"type": "stdio", "command": "user-cmd"}})
        project_cfg = _make_full_config({"foo": {"type": "stdio", "command": "project-cmd"}})

        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        bz_dir = fake_home / ".config" / "baozicode"
        bz_dir.mkdir(parents=True)
        (bz_dir / "config.yaml").write_text(yaml.dump(user_cfg), encoding="utf-8")
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=tmp_path
        ) as f:
            yaml.dump(project_cfg, f)
            project_path = f.name

        try:
            config = load_config(project_path)
            assert config.mcp_servers["foo"].command == "project-cmd"
        finally:
            os.unlink(project_path)

    def test_user_only_server_inherited(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """项目级没声明的 user-only server 应被保留。"""
        user_cfg = _make_full_config({"only_user": {"type": "stdio", "command": "user-cmd"}})
        project_cfg = _make_full_config({})

        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        bz_dir = fake_home / ".config" / "baozicode"
        bz_dir.mkdir(parents=True)
        (bz_dir / "config.yaml").write_text(yaml.dump(user_cfg), encoding="utf-8")
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=tmp_path
        ) as f:
            yaml.dump(project_cfg, f)
            project_path = f.name

        try:
            config = load_config(project_path)
            assert "only_user" in config.mcp_servers
            assert config.mcp_servers["only_user"].command == "user-cmd"
        finally:
            os.unlink(project_path)

    def test_empty_when_no_mcp_servers(self, tmp_path: Path) -> None:
        cfg_dict = _make_full_config(None)  # no mcp_servers
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, dir=tmp_path
        ) as f:
            yaml.dump(cfg_dict, f)
            path = f.name
        try:
            config = load_config(path)
            assert config.mcp_servers == {}
        finally:
            os.unlink(path)
