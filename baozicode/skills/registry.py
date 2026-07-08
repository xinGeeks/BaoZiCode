"""v1.0 Skills — 3 级目录扫描 + 优先级合并。

公开 API:
- `SkillRegistry.scan(builtin_dir, user_dir, project_dir, *, valid_tools=None)` —
  扫 3 个目录(都可选),合并成一个 `SkillRegistry`
- `SkillRegistry.lookup(name) -> SkillDef | None`
- `SkillRegistry.list_visible() -> list[(name, description, source)]`
- `SkillRegistry.reload(name)` — 显式热更新
- `SkillRegistry.scan_errors` — 收集的 (path, reason) tuple,boot 时可上 stderr

失败模式:
- 解析失败的文件 → 跳过,error 进 `scan_errors`,不进 registry
- `allowed-tools` 引用 `valid_tools` 之外的 tool → `SystemExit` (boot panic)
- 所有文件都失败 → registry 空 + `scan_errors` 全有,但不 panic
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from baozicode.skills.schema import SkillDef, parse_frontmatter


@dataclass(frozen=True)
class ScanError:
    """单文件解析失败的记录,boot 时可输出到 stderr。"""

    path: Path
    reason: str


class SkillRegistry:
    """3 级扫描合并的 Skill 注册中心。

    用法:
        reg = SkillRegistry.scan(
            builtin_dir=Path("baozicode/skills/builtin"),
            user_dir=Path.home() / ".config/baozicode/skills",
            project_dir=project_root / ".baozicode/skills",
            valid_tools={"Read", "Write", ...},
        )
        for name, desc, src in reg.list_visible():
            print(f"{name}: {desc}")
        def_ = reg.lookup("review")
    """

    def __init__(self) -> None:
        self._defs: dict[str, SkillDef] = {}
        self._scan_errors: list[ScanError] = []

    # ---- 构造 ----

    @classmethod
    def scan(
        cls,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
        project_dir: Path | None = None,
        *,
        valid_tools: Iterable[str] | None = None,
    ) -> "SkillRegistry":
        """扫 3 个目录(顺序: builtin → user → project),合并。

        Args:
            builtin_dir: 内置 Skill 根目录(通常 = `baozicode/skills/builtin`)
            user_dir: 用户级 Skill 根目录(通常 = `~/.config/baozicode/skills`)
            project_dir: 项目级 Skill 根目录(通常 = `<root>/.baozicode/skills`)
            valid_tools: 当前 ToolRegistry 的工具名集合,用于校验
                `allowed-tools`。None = 不校验(测试用)。

        Returns:
            新的 SkillRegistry 实例

        Raises:
            SystemExit: `allowed-tools` 引用了 `valid_tools` 之外的 tool
        """
        reg = cls()
        valid_set: set[str] | None = set(valid_tools) if valid_tools is not None else None

        # 按优先级低 → 高扫,后扫到的覆盖先扫到的
        for source, root in (
            ("builtin", builtin_dir),
            ("user", user_dir),
            ("project", project_dir),
        ):
            reg._scan_one_source(root, source, valid_set)

        return reg

    def _scan_one_source(
        self,
        root: Path | None,
        source: str,
        valid_tools: set[str] | None,
    ) -> None:
        if root is None or not root.exists() or not root.is_dir():
            return
        # 找一级子目录,每个子目录里找 SKILL.md
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            self._load_file(skill_md, source, valid_tools)

    def _load_file(
        self,
        path: Path,
        source: str,
        valid_tools: set[str] | None,
    ) -> None:
        try:
            text = path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(text, file_path=path)
        except ValueError as e:
            self._scan_errors.append(ScanError(path=path, reason=str(e)))
            return
        except OSError as e:
            self._scan_errors.append(
                ScanError(path=path, reason=f"读取失败: {e}")
            )
            return

        # allowed-tools 校验
        if valid_tools is not None and fm.allowed_tools is not None:
            unknown = [t for t in fm.allowed_tools if t not in valid_tools]
            if unknown:
                raise SystemExit(
                    f"skill '{fm.name}' allowed-tools 引用不存在的 tool: {unknown!r}"
                    f" (file: {path})"
                )

        sd = SkillDef(
            frontmatter=fm,
            body=body,
            source=source,  # type: ignore[arg-type]
            path=path,
        )
        self._defs[fm.name] = sd

    # ---- 查询 ----

    def lookup(self, name: str) -> SkillDef | None:
        return self._defs.get(name)

    def list_all(self) -> list[SkillDef]:
        """返回全部 Skills(按 name 字母序),含 hidden。"""
        return [self._defs[k] for k in sorted(self._defs)]

    def list_visible(self) -> list[tuple[str, str, str]]:
        """返回 (name, description, source) 元组列表,按 name 字母序,排除 hidden。

        主要用于:
        - boot 时把 name + description 灌进 system prompt
        - `/skill list` 命令显示
        """
        return [
            (sd.name, sd.description, sd.source)
            for sd in self.list_all()
            if not sd.hidden
        ]

    # ---- 状态 ----

    @property
    def scan_errors(self) -> list[ScanError]:
        """扫描期间收集的错误。boot 时可上 stderr。"""
        return list(self._scan_errors)

    def __len__(self) -> int:
        return len(self._defs)

    def __contains__(self, name: str) -> bool:
        return name in self._defs

    # ---- 热更新 ----

    def reload(self, name: str, *, valid_tools: Iterable[str] | None = None) -> SkillDef:
        """重新读盘 + 重新解析,替换 registry 里的 SkillDef。

        失败时保留旧版本(不抛错),并把 error 追加到 scan_errors。

        Returns:
            替换后的新 SkillDef

        Raises:
            KeyError: name 不在 registry 里
        """
        if name not in self._defs:
            raise KeyError(f"no such skill: {name!r}")
        old = self._defs[name]
        valid_set: set[str] | None = set(valid_tools) if valid_tools is not None else None
        # 暂时保存当前 defs,失败时回滚到旧的
        try:
            text = old.path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(text, file_path=old.path)
        except (ValueError, OSError) as e:
            self._scan_errors.append(
                ScanError(path=old.path, reason=f"reload 失败: {e}")
            )
            return old
        if valid_set is not None and fm.allowed_tools is not None:
            unknown = [t for t in fm.allowed_tools if t not in valid_set]
            if unknown:
                self._scan_errors.append(
                    ScanError(
                        path=old.path,
                        reason=f"reload 失败: allowed-tools 引用不存在的 tool: {unknown!r}",
                    )
                )
                return old
        new_sd = SkillDef(
            frontmatter=fm,
            body=body,
            source=old.source,  # source 不变
            path=old.path,
        )
        self._defs[name] = new_sd
        return new_sd


# ---- 工具 ----


def emit_scan_warnings(errors: list[ScanError]) -> None:
    """把 scan_errors 写到 stderr。

    调用方在 boot 时调一次(让用户知道哪些 Skill 加载失败)。
    """
    if not errors:
        return
    if len(errors) == 1:
        e = errors[0]
        print(
            f"WARN: skill 解析失败: {e.path}: {e.reason}",
            file=sys.stderr,
        )
    else:
        # 多条 → 单行汇总
        paths = ", ".join(str(e.path).replace("\\", "/") for e in errors)
        print(
            f"WARN: {len(errors)} 个 skill 解析失败: {paths}",
            file=sys.stderr,
        )


__all__ = ["ScanError", "SkillRegistry", "emit_scan_warnings"]
