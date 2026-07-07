"""stdio MCP transport — 启动子进程,stdin/stdout 走 JSON-RPC newline-delimited 帧。

设计上:
- `send(frame_str)` 写一行(frame + \\n)到 stdin(必须 caller 已经把
  request 序列化成 JSON)
- `recv_loop()` 是 async generator,从 stdout 一行一行 yield 解析后的 dict;
  遇到 EOF / 进程退出则退出 generator
- stderr 由独立的 `_stderr_drain` 任务持续读取并 log 到命名 logger,避免
  pipe buffer 填满阻塞子进程

参考 MCP spec 2025-11-25 stdio transport 章节。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any


class StdioTransport:
    """单个 MCP server 子进程的 stdio 传输。"""

    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        env: dict[str, str],
        cwd: str | None,
        logger_name: str,
    ) -> None:
        self._command = command
        self._args = args
        self._env = env
        self._cwd = cwd
        self._log = logging.getLogger(logger_name)
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        """启动子进程并起 stderr drain 任务。"""
        merged_env = {**os.environ, **self._env}
        # Windows 上 asyncio 默认 ProactorEventLoop 不支持 subprocess;
        # SelectorEventLoop 可以(create_subprocess_exec 会自动 fallback)。
        self._proc = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
            cwd=self._cwd,
        )
        self._log.debug(
            "stdio: subprocess started pid=%s command=%s args=%s",
            self._proc.pid, self._command, self._args,
        )
        self._stderr_task = asyncio.create_task(self._stderr_drain())

    async def _stderr_drain(self) -> None:
        """持续读 stderr,每行 log 到命名 logger。

        必须独立 task — 否则 pipe buffer 满后子进程会阻塞。
        """
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                if text:
                    self._log.debug("%s", text)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            self._log.debug("stderr drain error: %s", exc)

    async def send(self, frame_str: str) -> None:
        """写一行 newline-delimited JSON 帧到 stdin。"""
        if self._proc is None or self._proc.stdin is None or self._proc.stdin.is_closing():
            raise RuntimeError("stdio transport not started or already closed")
        data = (frame_str + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def recv_loop(self) -> AsyncIterator[dict[str, Any]]:
        """持续从 stdout 读 newline-delimited JSON 帧。

        generator 在 stdout EOF 或进程退出时自然结束。
        每行必须能被 json.loads 解析;解析失败 log warning 并跳过该行。
        """
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError as exc:
                self._log.warning("stdio: invalid JSON frame skipped: %s line=%r", exc, text[:200])

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and not self._closed

    async def close(self) -> None:
        """terminate 子进程(SIGTERM → 5s grace → SIGKILL),清理 stderr drain。"""
        if self._closed:
            return
        self._closed = True
        if self._proc is None:
            return
        proc = self._proc
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._log.warning("stdio: subprocess did not exit, sending SIGKILL")
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._log.error("stdio: subprocess still alive after SIGKILL")
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


__all__ = ["StdioTransport"]
