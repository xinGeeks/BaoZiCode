"""v0.7 磁盘 offload 存储 — `.baozicode/context/<session_id>/<block>.json`。

设计:
- 每个 session 一个目录(session_id 是 Agent 构造时生成的 UUID)
- 文件名:`<tool>_<hash8>_<counter>.json`
  - `hash8 = sha1(content.encode("utf-8"))[:8]`(同内容稳定,异内容大概率不同)
  - `counter` 在同 session 内单调递增(防止 hash 撞 + 同一 block 多次 offload)
- 内容:`{"content": <原内容>, "offloaded_at": iso8601, "tool": <name>, "tool_call_id": <id>}`
- `.gitignore` 自动加 `.baozicode/context/`(idempotent)
- cleanup 走 session 目录为单位,不会误伤其他 session
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from threading import Lock

__all__ = ["ContextStorage"]


_GITIGNORE_LINE = ".baozicode/context/"
_GITIGNORE_PATH = Path(".gitignore")


class ContextStorage:
    """单 session 的 offload 文件存储。"""

    def __init__(self, project_root: Path, session_id: str) -> None:
        self.project_root = project_root.resolve()
        self.session_id = session_id
        self.session_dir = self.project_root / ".baozicode" / "context" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._counter_lock = Lock()
        self._counter = count(1)
        self._ensure_gitignore()

    # ---- public API ----

    def write_block(
        self,
        tool_call_id: str,
        tool_name: str,
        content: str,
    ) -> Path:
        """写一个 offload 文件,返回相对项目根的路径(给 ToolResultBlock.offloaded_to 用)。"""
        hash8 = hashlib.sha1(content.encode("utf-8")).hexdigest()[:8]
        with self._counter_lock:
            counter = next(self._counter)
        filename = f"{tool_name}_{hash8}_{counter}.json"
        target = self.session_dir / filename
        payload = {
            "content": content,
            "offloaded_at": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "tool_call_id": tool_call_id,
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 返回相对 project_root 的路径(用 forward-slash,跟 gitignore 风格一致)
        rel = target.relative_to(self.project_root)
        return Path(str(rel).replace("\\", "/"))

    def cleanup(self) -> int:
        """删除本 session 目录下所有文件 + 空目录,返回删除的文件数。"""
        if not self.session_dir.is_dir():
            return 0
        count_removed = 0
        for f in self.session_dir.iterdir():
            if f.is_file():
                f.unlink()
                count_removed += 1
        # 尝试移除空目录(其他进程可能已经在写,失败不抛)
        try:
            self.session_dir.rmdir()
        except OSError:
            pass
        return count_removed

    def cleanup_all_sessions_except(self, keep_session_ids: set[str]) -> int:
        """Power user API:清掉本 project_root 下所有 session 子目录,保留指定 ids。"""
        context_root = self.project_root / ".baozicode" / "context"
        if not context_root.is_dir():
            return 0
        removed = 0
        for child in context_root.iterdir():
            if not child.is_dir():
                continue
            if child.name in keep_session_ids:
                continue
            for f in child.iterdir():
                if f.is_file():
                    f.unlink()
                    removed += 1
            try:
                child.rmdir()
            except OSError:
                pass
        return removed

    # ---- internals ----

    def _ensure_gitignore(self) -> None:
        """确保 `<project_root>/.gitignore` 含 `.baozicode/context/`(idempotent)。"""
        gitignore = self.project_root / _GITIGNORE_PATH
        if not gitignore.exists():
            # 没有 .gitignore 时不主动创建(用户可能用别的 vcs 工具),
            # 但项目级 offload 目录已经有 .baozicode 命名空间,不太可能 commit
            return
        try:
            existing = gitignore.read_text(encoding="utf-8")
        except OSError:
            return
        # 检查是否已有该行(任意位置、含尾注释)
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped == _GITIGNORE_LINE or stripped.startswith(_GITIGNORE_LINE + " "):
                return
        # 追加
        with self._counter_lock:  # reuse lock for fs write
            new_content = existing
            if not new_content.endswith("\n"):
                new_content += "\n"
            new_content += _GITIGNORE_LINE + "\n"
            gitignore.write_text(new_content, encoding="utf-8")
