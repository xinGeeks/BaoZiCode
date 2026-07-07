"""StdioTransport 单元测试。

用一个内嵌的 Python echo server 当假 MCP server:
- 读 stdin 的每行 frame,在 stdout 写回 `{... "echo": ...}` 响应
- 写一行到 stderr(供 stderr drain 验证)
- 收 SIGTERM 时优雅退出
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import textwrap

import pytest

from baozicode.mcp.transport_stdio import StdioTransport


# 内嵌 fake MCP server 脚本 — 写入临时文件后用同一 Python 解释器跑。
# 比写一个外部 fixture 文件简单,跨平台一致。
FAKE_SERVER_SCRIPT = textwrap.dedent(
    """
    import json
    import sys
    import signal

    # 写一行到 stderr 验证 drain
    print("starting up", file=sys.stderr, flush=True)

    def handle_sigterm(signum, frame):
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_sigterm)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        req_id = msg.get("id")
        method = msg.get("method")
        if req_id is None:
            continue  # 通知,不响应
        if method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {"name": "echo", "description": "echo arg",
                         "inputSchema": {"type": "object",
                                          "properties": {"text": {"type": "string"}}}}
                    ]
                },
            }
        elif method == "echo":
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "result": {"echo": msg.get("params", {})}}
        else:
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": "Method not found"}}
        print(json.dumps(resp), flush=True)
    """
).strip()


def _write_fake_server(tmp_path) -> str:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER_SCRIPT, encoding="utf-8")
    return str(script)


class TestStdioTransport:
    async def test_round_trip_request_response(self, tmp_path) -> None:
        script = _write_fake_server(tmp_path)
        t = StdioTransport(
            command=sys.executable,
            args=[script],
            env={},
            cwd=None,
            logger_name="test.mcp.fs",
        )
        await t.start()
        try:
            # 启动一个 task 持续读 recv_loop,收集前几个 frame
            received: list[dict] = []

            async def consume(n: int) -> None:
                gen = t.recv_loop()
                for _ in range(n):
                    received.append(await gen.__anext__())

            consumer = asyncio.create_task(consume(2))
            await asyncio.sleep(0.05)  # 让子进程就绪
            await t.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "echo",
                                     "params": {"text": "hi"}}))
            await t.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
            await asyncio.wait_for(consumer, timeout=5.0)

            assert len(received) == 2
            assert received[0]["id"] == 1
            assert received[0]["result"]["echo"] == {"text": "hi"}
            assert received[1]["id"] == 2
            assert received[1]["result"]["tools"][0]["name"] == "echo"
        finally:
            await t.close()

    async def test_stderr_drained_to_named_logger(self, tmp_path, caplog) -> None:
        script = _write_fake_server(tmp_path)
        logger_name = "test.mcp.stderr.fs"
        # caplog 默认只接 WARNING+;开 DEBUG 并 propagate
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
        caplog.set_level(logging.DEBUG, logger=logger_name)
        t = StdioTransport(
            command=sys.executable,
            args=[script],
            env={},
            cwd=None,
            logger_name=logger_name,
        )
        await t.start()
        # 等 stderr drain 拿到 "starting up"
        await asyncio.sleep(0.3)
        await t.close()
        records = [r for r in caplog.records if r.name == logger_name]
        assert any("starting up" in r.getMessage() for r in records), \
            f"expected stderr line in logger; got: {[r.getMessage() for r in records]}"

    async def test_close_terminates_subprocess_within_grace(self, tmp_path) -> None:
        script = _write_fake_server(tmp_path)
        t = StdioTransport(
            command=sys.executable,
            args=[script],
            env={},
            cwd=None,
            logger_name="test.mcp.close",
        )
        await t.start()
        assert t.is_alive()
        await t.close()
        assert not t.is_alive()

    async def test_eof_on_stdout_ends_recv_loop(self, tmp_path) -> None:
        """子进程读 stdin 关闭后退出,recv_loop 应该自然结束。"""
        # 写一个简单的 server:stdin 关闭 → 退出
        simple_script = tmp_path / "simple.py"
        simple_script.write_text(
            "import sys\n"
            "for line in sys.stdin:\n"
            "    pass\n",
            encoding="utf-8",
        )
        t = StdioTransport(
            command=sys.executable,
            args=[str(simple_script)],
            env={},
            cwd=None,
            logger_name="test.mcp.eof",
        )
        await t.start()
        # 关 stdin(写入 EOF)→ 子进程退出 → recv_loop 应该结束
        assert t._proc is not None and t._proc.stdin is not None
        t._proc.stdin.close()
        frames: list[dict] = []
        async for f in t.recv_loop():
            frames.append(f)
        assert frames == []
        await t.close()

    async def test_invalid_json_line_skipped(self, tmp_path) -> None:
        """子进程写非法 JSON 行,transport log warning 并继续(不抛)。"""
        bad_script = tmp_path / "bad.py"
        bad_script.write_text(
            'import sys\n'
            'sys.stdout.write("not json\\n")\n'
            'sys.stdout.write(\'{"jsonrpc": "2.0", "id": 1, "result": {}}\\n\')\n'
            'sys.stdout.flush()\n'
            'sys.exit(0)\n',
            encoding="utf-8",
        )
        t = StdioTransport(
            command=sys.executable,
            args=[str(bad_script)],
            env={},
            cwd=None,
            logger_name="test.mcp.bad",
        )
        await t.start()
        frames: list[dict] = []
        async for f in t.recv_loop():
            frames.append(f)
        # bad 行被跳过,有效行收到
        assert len(frames) == 1
        assert frames[0]["id"] == 1
        await t.close()
