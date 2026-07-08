"""v0.7 ContextStorage 单元测试 — write / cleanup / gitignore / hash 稳定。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baozicode.context.storage import ContextStorage, _GITIGNORE_LINE


def test_write_block_creates_file_in_session_dir(
    context_storage: ContextStorage, tmp_project_root: Path
) -> None:
    """write_block returns a relative path under <project>/.baozicode/context/<sess>/."""
    rel = context_storage.write_block("t1", "Read", "hello world")
    assert rel.is_absolute() is False
    # rel 形如 .baozicode/context/<sess>/Read_<hash8>_<n>.json
    parts = rel.parts
    assert parts[0] == ".baozicode"
    assert parts[1] == "context"
    assert parts[2] == context_storage.session_id
    assert parts[3].startswith("Read_")
    assert parts[3].endswith(".json")
    # 物理文件存在
    full = tmp_project_root / rel
    assert full.is_file()


def test_write_block_file_has_required_json_keys(
    context_storage: ContextStorage,
) -> None:
    rel = context_storage.write_block("call-xyz", "Read", "data")
    payload = json.loads((context_storage.project_root / rel).read_text("utf-8"))
    assert payload["content"] == "data"
    assert payload["tool"] == "Read"
    assert payload["tool_call_id"] == "call-xyz"
    assert "offloaded_at" in payload
    # iso8601-ish
    assert "T" in payload["offloaded_at"]


def test_write_block_hash8_stable_for_same_content(context_storage: ContextStorage) -> None:
    """同内容 → 同 hash8(虽然 counter 不同,但 hash 部分稳定)。"""
    rel1 = context_storage.write_block("t1", "Read", "stable content")
    rel2 = context_storage.write_block("t2", "Read", "stable content")
    # 提取 hash8
    def hash8_of(p: Path) -> str:
        return p.stem.split("_")[1]

    assert hash8_of(rel1) == hash8_of(rel2)


def test_write_block_different_content_different_hash(context_storage: ContextStorage) -> None:
    rel1 = context_storage.write_block("t1", "Read", "alpha")
    rel2 = context_storage.write_block("t1", "Read", "beta")
    assert rel1 != rel2


def test_cleanup_removes_session_files_only(
    tmp_project_root: Path, context_storage: ContextStorage
) -> None:
    """cleanup 删除本 session 目录下的所有文件 + 空目录,不影响其他 session。"""
    # 写两个 block
    rel1 = context_storage.write_block("t1", "Read", "a")
    rel2 = context_storage.write_block("t2", "Read", "b")
    assert (tmp_project_root / rel1).is_file()
    # 创建另一个 session 目录(模拟并发)
    other = ContextStorage(project_root=tmp_project_root, session_id="other-session")
    other_rel = other.write_block("t3", "Read", "x")
    assert (tmp_project_root / other_rel).is_file()
    # cleanup
    removed = context_storage.cleanup()
    assert removed == 2
    assert not (tmp_project_root / rel1).exists()
    assert not (tmp_project_root / rel2).exists()
    # session 目录也清掉
    assert not (tmp_project_root / ".baozicode" / "context" / context_storage.session_id).is_dir()
    # other session 不受影响
    assert (tmp_project_root / other_rel).is_file()


def test_gitignore_line_added_idempotently(
    gitignore_present: Path,
) -> None:
    """_ensure_gitignore 加一行 + 第二次不重复加。"""
    gi = gitignore_present / ".gitignore"
    content_before = gi.read_text("utf-8")
    assert _GITIGNORE_LINE not in content_before
    # 第一次创建 storage → 触发 _ensure_gitignore
    ContextStorage(project_root=gitignore_present, session_id="session-2")
    after_first = gi.read_text("utf-8")
    assert _GITIGNORE_LINE in after_first
    # 第二次创建 storage → 不重复加
    ContextStorage(project_root=gitignore_present, session_id="session-3")
    after_second = gi.read_text("utf-8")
    # 行数只 +1,不重复
    assert after_first.count(_GITIGNORE_LINE) == 1
    assert after_second.count(_GITIGNORE_LINE) == 1
