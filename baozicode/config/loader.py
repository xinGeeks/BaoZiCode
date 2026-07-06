"""YAML 配置加载器。

加载顺序：
1. `--config <path>` 命令行参数
2. `./config.yaml`
3. `~/.config/baozicode/config.yaml`

加载步骤：
1. 调 `python-dotenv` 加载当前目录的 `.env`
2. 解析 YAML
3. 遍历数据结构中的字符串值，把 `${VAR}` 占位符替换为环境变量（仅在 string 中）
4. 用 Pydantic 校验
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from baozicode.config.schema import AppConfig

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

_CONFIG_CANDIDATES = (
    Path.home() / ".config" / "baozicode" / "config.yaml",
    Path("config.yaml"),
)


def _substitute_env(value: Any) -> Any:
    """递归地把 dict / list / str 中的 ${VAR} 占位符替换为环境变量。

    仅处理 dict、list、str 这几种容器；其它类型原样返回。
    """
    if isinstance(value, str):
        return _substitute_string(value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def _substitute_string(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        env_value = os.environ.get(name)
        if env_value is None:
            raise ConfigError(
                f"环境变量 {name} 未定义。"
                f"请在 .env 文件中设置 {name}，或先 export {name}=..."
            )
        return env_value

    return _ENV_PLACEHOLDER.sub(_replace, text)


def _resolve_config_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"指定的配置文件不存在: {path}")
        return path

    for candidate in _CONFIG_CANDIDATES:
        if candidate.is_file():
            return candidate

    raise ConfigError(
        "找不到配置文件。请在以下位置之一创建 config.yaml：\n"
        f"  - {Path('config.yaml').resolve()}\n"
        f"  - {Path.home() / '.config' / 'baozicode' / 'config.yaml'}\n"
        "可以复制项目根目录的 config.example.yaml 作为起点。"
    )


def load_config(explicit_path: str | None = None) -> AppConfig:
    """加载并校验配置。"""
    load_dotenv()

    path = _resolve_config_path(explicit_path)
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件 {path} 内容不是合法的 YAML 对象。")
    substituted = _substitute_env(data)
    return AppConfig.model_validate(substituted)


class ConfigError(RuntimeError):
    """配置加载/解析错误。"""
