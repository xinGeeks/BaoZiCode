"""Bash cwd 三状态机测试 — 验证逃逸保护。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.tools import bash as bash_mod
from baozicode.tools.bash import BashSession


def _make_session() -> BashSession:
    """构造一个 project_root 为 cwd 的会话。"""
    import os
    root = Path(os.getcwd()).resolve()
    return BashSession(root)


def test_cd_relative_escape_rejected() -> None:
    """cd ../../../etc 应该被拒,cwd 不变。"""
    s = _make_session()
    new_cwd, err = s.plan_cd("cd ../../../etc")
    assert err is not None
    assert "escapes" in err
    assert s.cwd == s.project_root
    print("[OK] cd ../../../etc rejected")


def test_cd_absolute_outside_rejected() -> None:
    """cd /tmp(或 Windows 的 C:\\)应该被拒。"""
    s = _make_session()
    if Path("/tmp").exists():
        new_cwd, err = s.plan_cd("cd /tmp")
        assert err is not None
    print("[OK] cd /tmp rejected")


def test_cd_within_root_allowed() -> None:
    """cd src(假设 src 在 root 下)应该允许。"""
    s = _make_session()
    src = s.project_root / "src"
    src.mkdir(exist_ok=True)
    new_cwd, err = s.plan_cd("cd src && ls")
    assert err is None
    assert new_cwd == src
    print("[OK] cd src && ls allowed")


def test_cd_chained_tracked() -> None:
    """cd src && cd sub(假设 sub 是 src 子目录)→ 最终 cwd 是 sub。"""
    s = _make_session()
    sub = s.project_root / "src" / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    new_cwd, err = s.plan_cd("cd src && cd sub")
    assert err is None
    assert new_cwd == sub
    print("[OK] chained cd tracked")


def test_cd_tilde_rejected() -> None:
    """cd ~/etc 展开到 home → 拒。"""
    s = _make_session()
    new_cwd, err = s.plan_cd("cd ~")
    assert err is not None
    print("[OK] cd ~ rejected")


def test_cd_dash_rejected() -> None:
    """cd - 引用 OLDPWD → 拒(我们不追踪)。"""
    s = _make_session()
    new_cwd, err = s.plan_cd("cd -")
    assert err is not None
    print("[OK] cd - rejected")


def test_cd_with_dollar_rejected() -> None:
    """cd $HOME 包含 shell 变量 → 拒(我们不展开)。"""
    s = _make_session()
    new_cwd, err = s.plan_cd("cd $HOME")
    assert err is not None
    print("[OK] cd $HOME rejected")


def test_cd_after_no_cd_keeps_cwd() -> None:
    """纯 echo 命令(没有 cd)→ cwd 不变。"""
    s = _make_session()
    new_cwd, err = s.plan_cd("echo hello")
    assert err is None
    assert new_cwd == s.cwd == s.project_root
    print("[OK] no cd: cwd unchanged")


if __name__ == "__main__":
    test_cd_relative_escape_rejected()
    test_cd_absolute_outside_rejected()
    test_cd_within_root_allowed()
    test_cd_chained_tracked()
    test_cd_tilde_rejected()
    test_cd_dash_rejected()
    test_cd_with_dollar_rejected()
    test_cd_after_no_cd_keeps_cwd()
    print("\nAll bash cwd tests passed.")