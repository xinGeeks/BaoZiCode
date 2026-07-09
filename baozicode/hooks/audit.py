"""Hook 审计日志:JSONL 写入 .baozicode/audit.log。

HookInvocation dataclass:
  timestamp / event / hook_id / action_kind / tool_name / tool_call_id /
  deny / reason / duration_ms / error

写入策略:
- aiofiles append,无 fsync(避免阻塞主循环)
- 启动期检查 size,超过 100MB → rotate 到 audit.log.YYYYMMDD-HHMMSS
- Agent.run finally 块写一条 final invocation(action_kind="pipeline")
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)


@dataclass
class HookInvocation:
    """单条 hook 触发记录。"""

    timestamp: str = ""
    event: str = ""
    hook_id: str = ""
    action_kind: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    deny: bool = False
    reason: str | None = None
    duration_ms: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = _now_iso()

    def to_json(self) -> str:
        d = asdict(self)
        # reason / error 为 None 时省略(jsonl 紧凑)
        return json.dumps({k: v for k, v in d.items() if v is not None}, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class HookAuditLog:
    """JSONL 审计写入器,异步 append。"""

    DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100MB

    def __init__(
        self,
        log_path: Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._path = Path(log_path)
        self._max_bytes = max_bytes
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def rotate_if_needed(self) -> None:
        """启动期同步调用一次:超 size 切到 .YYYYMMDD-HHMMSS。"""
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        if size >= self._max_bytes:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            rotated = self._path.with_name(f"{self._path.name}.{stamp}")
            try:
                self._path.rename(rotated)
                log.info("hook audit log rotated: %s → %s", self._path, rotated)
            except OSError as exc:
                log.warning("hook audit log 旋转失败: %s", exc)

    async def record_invocation(self, invocation: HookInvocation) -> None:
        """异步追加一行 JSONL;失败仅 log,不抛。"""
        line = invocation.to_json()
        async with self._lock:
            try:
                # 用同步 IO(aiofiles 不一定 install);asyncio.to_thread 包装
                await asyncio.to_thread(self._append_line, line)
            except Exception as exc:
                log.warning("hook audit 写入失败: %s", exc)

    def _append_line(self, line: str) -> None:
        # 行尾加 \n(若没有)
        if not line.endswith("\n"):
            line = line + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)

    def record_invocation_sync(self, invocation: HookInvocation) -> None:
        """同步版本,用于 finally 块(不阻塞退出)。"""
        line = invocation.to_json()
        try:
            self._append_line(line)
        except Exception as exc:
            log.warning("hook audit 写入失败(sync): %s", exc)


__all__ = ["HookAuditLog", "HookInvocation"]
