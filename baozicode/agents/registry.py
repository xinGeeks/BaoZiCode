"""v1.2 SubAgent Delegation — 4 级目录扫描 + 优先级合并。

公开 API:
- `AgentRegistry.scan(builtin_dir, user_dir, project_dir, *, plugin_agents=[], valid_tools=None)`
  — 扫 4 个来源,合并成一个 registry
- `AgentRegistry.lookup(name)` / `list_visible()` / `list_all()` / `reload(name)`
- `ScanError` — 单文件解析失败的记录
- `emit_scan_warnings(errors)` — 把 scan_errors 写到 stderr

加载顺序(后扫覆盖先扫):builtin → user → project → plugin。
同名后者完全覆盖前者(不合并 frontmatter,跟 Skill 一致)。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from baozicode.agents.schema import AgentDef, parse_agent


@dataclass(frozen=True)
class ScanError:
    """单文件解析失败的记录,boot 时可输出到 stderr。"""

    path: Path
    reason: str


class AgentRegistry:
    """4 级扫描合并的 Agent 注册中心。

    用法:
        reg = AgentRegistry.scan(
            builtin_dir=Path("baozicode/agents/builtin"),
            user_dir=Path.home() / ".config/baozicode/agents",
            project_dir=project_root / ".baozicode/agents",
            plugin_agents=[<AgentDef from MCP>],
            valid_tools={"Read", "Write", ...},
        )
        for name, desc, src in reg.list_visible():
            print(f"{name}: {desc} ({src})")
        def_ = reg.lookup("explorer")
    """

    def __init__(self) -> None:
        self._defs: dict[str, AgentDef] = {}
        self._scan_errors: list[ScanError] = []

    # ---- 构造 ----

    @classmethod
    def scan(
        cls,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        project_dir: Path | None = None,
        *,
        plugin_agents: Iterable[AgentDef] | None = None,
        valid_tools: Iterable[str] | None = None,
    ) -> "AgentRegistry":
        """扫 4 个来源(顺序: builtin → user → project → plugin),合并。

        Args:
            builtin_dir: 内置 Agent 根目录(通常 = `baozicode/agents/builtin`)
            user_dir: 用户级 Agent 根目录(通常 = `~/.config/baozicode/agents`)
            project_dir: 项目级 Agent 根目录(通常 = `<root>/.baozicode/agents`)
            plugin_agents: MCP plugin 拉取的 AgentDef 列表(可选)
            valid_tools: 当前 ToolRegistry 的工具名集合,用于校验
                `tools` / `tools-deny`。None = 不校验(测试用)。

        Returns:
            新的 AgentRegistry 实例

        Raises:
            SystemExit: `tools` / `tools-deny` 引用了 `valid_tools` 之外的 tool
        """
        reg = cls()
        valid_set: set[str] | None = (
            set(valid_tools) if valid_tools is not None else None
        )

        # 按优先级低 → 高扫,后扫到的覆盖先扫到的
        for source, root in (
            ("builtin", builtin_dir),
            ("user", user_dir),
            ("project", project_dir),
        ):
            reg._scan_one_source(root, source, valid_set)

        # plugin 是一组 AgentDef(已经解析过,直接 add)
        if plugin_agents:
            for ad in plugin_agents:
                if valid_set is not None:
                    if ad.frontmatter.tools is not None:
                        unknown = [
                            t for t in ad.frontmatter.tools if t not in valid_set
                        ]
                        if unknown:
                            raise SystemExit(
                                f"plugin agent '{ad.name}' tools 引用不存在的"
                                f" tool: {unknown!r}"
                            )
                    if ad.frontmatter.tools_deny is not None:
                        unknown = [
                            t
                            for t in ad.frontmatter.tools_deny
                            if t not in valid_set
                        ]
                        if unknown:
                            raise SystemExit(
                                f"plugin agent '{ad.name}' tools-deny"
                                f" 引用不存在的 tool: {unknown!r}"
                            )
                reg._defs[ad.name] = ad

        return reg

    def _scan_one_source(
        self,
        root: Path | None,
        source: str,
        valid_tools: set[str] | None,
    ) -> None:
        if root is None or not root.exists() or not root.is_dir():
            return
        # 找一级子目录,每个子目录里找 AGENT.md
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            agent_md = child / "AGENT.md"
            if not agent_md.is_file():
                continue
            self._load_file(agent_md, source, valid_tools)

    def _load_file(
        self,
        path: Path,
        source: str,
        valid_tools: set[str] | None,
    ) -> None:
        try:
            text = path.read_text(encoding="utf-8")
            fm, body = parse_agent(text, file_path=path)
        except ValueError as e:
            self._scan_errors.append(ScanError(path=path, reason=str(e)))
            return
        except OSError as e:
            self._scan_errors.append(
                ScanError(path=path, reason=f"读取失败: {e}")
            )
            return

        # tools / tools-deny 校验
        if valid_tools is not None:
            for field_name, tools_list in (
                ("tools", fm.tools),
                ("tools-deny", fm.tools_deny),
            ):
                if tools_list is None:
                    continue
                unknown = [t for t in tools_list if t not in valid_tools]
                if unknown:
                    raise SystemExit(
                        f"agent '{fm.name}' {field_name} 引用不存在的"
                        f" tool: {unknown!r} (file: {path})"
                    )

        ad = AgentDef(
            frontmatter=fm,
            body=body,
            source=source,  # type: ignore[arg-type]
            path=path,
        )
        self._defs[fm.name] = ad

    # ---- 查询 ----

    def lookup(self, name: str) -> AgentDef | None:
        return self._defs.get(name)

    def list_all(self) -> list[AgentDef]:
        """返回全部 Agents(按 name 字母序),含 hidden。"""
        return [self._defs[k] for k in sorted(self._defs)]

    def list_visible(self) -> list[tuple[str, str, str]]:
        """返回 (name, description, source) 元组列表,按 name 字母序,排除 hidden。

        主要用于:
        - boot 时把 name + description 灌进 system prompt 的「可用 Agent」段
        - `/agent list` 命令显示
        """
        return [
            (ad.name, ad.description, ad.source)
            for ad in self.list_all()
            if not ad.hidden
        ]

    # ---- 状态 ----

    @property
    def scan_errors(self) -> list[ScanError]:
        return list(self._scan_errors)

    def __len__(self) -> int:
        return len(self._defs)

    def __contains__(self, name: str) -> bool:
        return name in self._defs

    # ---- 热更新 ----

    def reload(
        self,
        name: str,
        *,
        valid_tools: Iterable[str] | None = None,
    ) -> AgentDef:
        """重新读盘 + 重新解析,替换 registry 里的 AgentDef。

        失败时保留旧版本(不抛错),并把 error 追加到 scan_errors。

        Returns:
            替换后的新 AgentDef

        Raises:
            KeyError: name 不在 registry 里
        """
        if name not in self._defs:
            raise KeyError(f"no such agent: {name!r}")
        old = self._defs[name]
        valid_set: set[str] | None = (
            set(valid_tools) if valid_tools is not None else None
        )
        # 失败时回滚到旧版
        try:
            text = old.path.read_text(encoding="utf-8")
            fm, body = parse_agent(text, file_path=old.path)
        except (ValueError, OSError) as e:
            self._scan_errors.append(
                ScanError(path=old.path, reason=f"reload 失败: {e}")
            )
            return old
        if valid_set is not None:
            for field_name, tools_list in (
                ("tools", fm.tools),
                ("tools-deny", fm.tools_deny),
            ):
                if tools_list is None:
                    continue
                unknown = [t for t in tools_list if t not in valid_set]
                if unknown:
                    self._scan_errors.append(
                        ScanError(
                            path=old.path,
                            reason=(
                                f"reload 失败:{field_name} 引用不存在的"
                                f" tool: {unknown!r}"
                            ),
                        )
                    )
                    return old
        new_ad = AgentDef(
            frontmatter=fm,
            body=body,
            source=old.source,  # source 不变
            path=old.path,
        )
        self._defs[name] = new_ad
        return new_ad


# ---- 工具 ----


def emit_scan_warnings(errors: list[ScanError]) -> None:
    """把 scan_errors 写到 stderr。调用方在 boot 时调一次。"""
    if not errors:
        return
    if len(errors) == 1:
        e = errors[0]
        print(
            f"WARN: agent 解析失败: {e.path}: {e.reason}",
            file=sys.stderr,
        )
    else:
        paths = ", ".join(str(e.path).replace("\\", "/") for e in errors)
        print(
            f"WARN: {len(errors)} 个 agent 解析失败: {paths}",
            file=sys.stderr,
        )


__all__ = ["AgentRegistry", "ScanError", "emit_scan_warnings"]
