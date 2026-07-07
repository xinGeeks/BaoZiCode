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

v0.5 新增:
- 还会搜索主 config 所在目录下的 `permissions*.yaml` sidecar 文件,
  解析后合并到 `AppConfig.permissions_v5`(Pydantic 模型)。
- 这些 sidecar 与 `permissions/loader.py` 的三层 YAML 互补:
  sidecar 走"声明式"路径,三层 YAML 走"运行时文件系统"路径。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from baozicode.config.schema import AppConfig, PermissionsV5

log = logging.getLogger(__name__)

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


def _discover_permissions_yaml(config_path: Path) -> PermissionsV5 | None:
    """v0.5:在主 config 所在目录搜索 `permissions*.yaml` sidecar。

    解析所有 sidecar,合并 mode / rules;sidecar 覆盖 base(sidecar 文件按字母序,
    后者覆盖前者)。

    失败策略:文件读不出 / YAML 坏 / 字段不全 → 静默跳过 + log warning。
    """
    search_dir = config_path.parent
    if not search_dir.is_dir():
        return None
    # 找 permissions*.yaml(包含 permissions.yaml / permissions.local.yaml / 等)
    candidates = sorted(search_dir.glob("permissions*.yaml"))
    if not candidates:
        return None

    merged_mode: str = "default"
    merged_rules: list = []
    loaded_paths: list[str] = []
    for path in candidates:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            log.warning("config: %s 解析失败,跳过: %s", path, exc)
            continue
        if not isinstance(data, dict):
            log.warning("config: %s 顶层不是 dict,跳过", path)
            continue
        mode_raw = data.get("mode")
        if isinstance(mode_raw, str) and mode_raw in (
            "strict", "default", "permissive"
        ):
            merged_mode = mode_raw
        elif mode_raw is not None:
            log.warning(
                "config: %s mode=%r 非法,沿用 default", path, mode_raw,
            )
        for entry in data.get("rules", []) or []:
            if not isinstance(entry, dict):
                log.warning("config: %s 规则不是 dict,跳过: %r", path, entry)
                continue
            tool = entry.get("tool")
            pattern = entry.get("pattern")
            decision = entry.get("decision")
            if not (isinstance(tool, str) and tool):
                log.warning("config: %s 规则缺 tool,跳过: %r", path, entry)
                continue
            if not (isinstance(pattern, str) and pattern):
                log.warning("config: %s 规则缺 pattern,跳过: %r", path, entry)
                continue
            if decision not in ("allow", "deny"):
                log.warning(
                    "config: %s 规则缺/非法的 decision,跳过: %r", path, entry,
                )
                continue
            merged_rules.append(entry)
        loaded_paths.append(str(path))

    if not loaded_paths:
        return None
    log.info(
        "config: 从 sidecar 合并了 %d 个 permissions*.yaml: %s",
        len(loaded_paths), loaded_paths,
    )
    return PermissionsV5(
        mode=merged_mode,  # type: ignore[arg-type]
        rules=merged_rules,
    )


def _merge_mcp_servers_layered(path: Path) -> dict[str, Any]:
    """v0.6:对 `mcp_servers` 做两层 merge(project + user,project 优先)。

    返回 raw 字典(含 `${VAR}` 占位符,调用方再走 _substitute_env)。
    仅合并 `mcp_servers` 顶层键;其它键不动 — 整份 config 的两层合并
    在更高层做。

    实现:
    1. 读主 config(已 resolve)取 `mcp_servers`
    2. 读 ~/.config/baozicode/config.yaml 取 `mcp_servers`(若存在)
    3. 两层 dict 合并:同 key 时 main(项目级)覆盖 user(用户级)
    4. 若同 server 在两层都声明且 `type` 不同,log warning(项目级胜)
    """
    main_data: dict[str, Any] = {}
    try:
        raw = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            main_data = loaded
    except (OSError, yaml.YAMLError) as exc:
        log.warning("config: %s 读取失败,跳过主 mcp_servers: %s", path, exc)

    user_path = Path.home() / ".config" / "baozicode" / "config.yaml"
    user_mcp: dict[str, Any] = {}
    if user_path.is_file() and user_path.resolve() != path.resolve():
        try:
            raw_user = user_path.read_text(encoding="utf-8")
            loaded_user = yaml.safe_load(raw_user)
            if isinstance(loaded_user, dict):
                user_section = loaded_user.get("mcp_servers")
                if isinstance(user_section, dict):
                    user_mcp = user_section
        except (OSError, yaml.YAMLError) as exc:
            log.warning("config: %s 读取失败,跳过用户级 mcp_servers: %s", user_path, exc)

    main_section = main_data.get("mcp_servers")
    if not isinstance(main_section, dict):
        main_section = {}

    if not user_mcp and not main_section:
        return {}

    merged: dict[str, Any] = dict(user_mcp)
    for name, main_cfg in main_section.items():
        if name in merged:
            user_type = merged[name].get("type") if isinstance(merged[name], dict) else None
            main_type = main_cfg.get("type") if isinstance(main_cfg, dict) else None
            if user_type and main_type and user_type != main_type:
                log.warning(
                    "config: mcp_servers.%s 在用户级(type=%s)和项目级(type=%s)"
                    "声明了不同的 transport,以项目级为准",
                    name, user_type, main_type,
                )
        merged[name] = main_cfg

    if user_mcp:
        log.info(
            "config: mcp_servers 两层合并完成 — 用户级 %d 条,项目级 %d 条,合并后 %d 条",
            len(user_mcp), len(main_section), len(merged),
        )
    return merged


def load_config(explicit_path: str | None = None) -> AppConfig:
    """加载并校验配置。"""
    load_dotenv()

    path = _resolve_config_path(explicit_path)
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件 {path} 内容不是合法的 YAML 对象。")

    # v0.6:在 `_substitute_env` 之前先做 mcp_servers 两层 merge,
    # 这样 `${VAR}` 替换对合并后的 command/args/env/url/headers 都生效。
    merged_mcp = _merge_mcp_servers_layered(path)
    if merged_mcp:
        data = dict(data)
        data["mcp_servers"] = merged_mcp

    substituted = _substitute_env(data)
    config = AppConfig.model_validate(substituted)

    # v0.5:同时存在 v0.2 `permissions:` 与 v0.5 `permissions_v5:` → 记 deprecation
    # 警告(两个块都被保留;运行时由 App 决定走哪条 — 没 merged_permissions 时
    # 走 v0.2,有则走 v0.5)
    if config.permissions is not None and config.permissions_v5 is not None:
        log.warning(
            "config: 同时声明 v0.2 `permissions` 与 v0.5 `permissions_v5`,"
            "v0.2 字段将被忽略,迁移见 README '权限系统' 章节"
        )

    # v0.5:sidecar permissions*.yaml 合并到 permissions_v5
    sidecar = _discover_permissions_yaml(path)
    if sidecar is not None:
        if config.permissions_v5 is not None:
            # config.yaml 和 sidecar 都给了 v0.5 块 → sidecar 覆盖
            log.warning(
                "config: config.yaml 与 sidecar permissions*.yaml 同时声明 "
                "permissions_v5,以 sidecar 为准"
            )
        # sidecar.rules 是 dict 列表,需直接赋值给 Pydantic 模型
        config = config.model_copy(update={"permissions_v5": sidecar})

    return config


class ConfigError(RuntimeError):
    """配置加载/解析错误。"""
