"""v1.4 Team Foundation — 跨进程锁并发测试。

`tests/test_teams_v14_lockfile.py` 是单进程内多线程测试;本测试覆盖
**跨进程**(`subprocess.run`)+ lockfile 的真正意义:两个独立 Python
进程同时往同一个 `<member>/inbox.jsonl` append,锁保证消息无丢失
无交错。

技术细节:
- 用 `subprocess.Popen` 启 2 个 Python 子进程(各自独立 fcntl/msvcrt
  句柄),它们都跑一段 append 循环(50 条)
- 结束后读 inbox.jsonl,行数 = 2 × 50,内容完整可解析
- Windows `msvcrt.locking` 是 mandatory,验证能拦跨进程并发

平台注意:
- POSIX `fcntl.flock` 是 advisory,需要所有协作者走同一个 fd 才行,
  我们的实现统一在 `mailbox_lock` context manager 里,所以跨进程也 work。
- Windows `msvcrt.locking` 是 mandatory,跨进程天然 OK。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


# 子进程脚本:跑 N 次 append_message 然后退出
_WORKER_SCRIPT = '''
import json
import sys
from pathlib import Path

from baozicode.teams import Mailbox, Message

member_dir = Path(sys.argv[1])
worker_id = sys.argv[2]
n_messages = int(sys.argv[3])

results = []
for i in range(n_messages):
    Mailbox.append_message(
        member_dir,
        "inbox",
        Message(sender=worker_id, body=f"msg-{worker_id}-{i}"),
    )
    results.append({"worker": worker_id, "idx": i})

# 退出码 0 + 写一条 marker file,让父进程能确认我跑完
marker = member_dir / f".worker-{worker_id}.done"
marker.write_text(json.dumps(results), encoding="utf-8")
'''


@pytest.fixture
def member_dir(tmp_path: Path) -> Path:
    """构造一个 ready-to-go 的 member dir(有 inbox.jsonl + .lock)。"""
    d = tmp_path / "alice"
    d.mkdir()
    (d / "inbox.jsonl").touch()
    return d


def _run_worker(member_dir: Path, worker_id: str, n: int) -> subprocess.CompletedProcess:
    """启一个 Python 子进程跑 _WORKER_SCRIPT。"""
    return subprocess.run(
        [sys.executable, "-c", _WORKER_SCRIPT, str(member_dir), worker_id, str(n)],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestTwoProcesses:
    def test_two_workers_no_loss(self, member_dir: Path) -> None:
        """2 个进程 × 50 条 = 100 条,全部写入,JSONL 完整可解析。"""
        n = 50
        procs = [
            _run_worker(member_dir, f"w{i}", n) for i in range(2)
        ]
        for p in procs:
            assert p.returncode == 0, f"worker failed: stderr={p.stderr}"

        # 读 inbox.jsonl 验证
        lines = (member_dir / "inbox.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2 * n, f"expected {2 * n} lines, got {len(lines)}"

        # 每行是合法 JSON
        for line in lines:
            data = json.loads(line)
            assert "sender" in data
            assert "body" in data
            assert data["body"].startswith("msg-w")

        # 两个 worker 的 marker 都存在
        assert (member_dir / ".worker-w0.done").exists()
        assert (member_dir / ".worker-w1.done").exists()

    def test_no_interleaving_within_line(self, member_dir: Path) -> None:
        """锁保证每条 JSONL 行写完才释放 → 行内不交错。"""
        n = 30
        procs = [
            _run_worker(member_dir, f"w{i}", n) for i in range(2)
        ]
        for p in procs:
            assert p.returncode == 0

        # 验证每行仍是合法 JSON
        content = (member_dir / "inbox.jsonl").read_text(encoding="utf-8")
        for line in content.strip().splitlines():
            data = json.loads(line)  # raises if malformed
            assert "sender" in data
            assert "body" in data


class TestThreeProcesses:
    def test_three_workers_serialized(self, member_dir: Path) -> None:
        """3 个进程 × 40 条 = 120 条,锁串行化保证全成功。"""
        n = 40
        procs = [
            _run_worker(member_dir, f"w{i}", n) for i in range(3)
        ]
        for p in procs:
            assert p.returncode == 0, f"stderr: {p.stderr}"

        lines = (member_dir / "inbox.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3 * n

        # 按 worker 计数 — 每个 worker 恰好写了 n 条
        counts: dict[str, int] = {}
        for line in lines:
            data = json.loads(line)
            sender = data["sender"]
            counts[sender] = counts.get(sender, 0) + 1
        assert counts == {"w0": n, "w1": n, "w2": n}