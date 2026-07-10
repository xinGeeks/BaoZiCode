# Tasks — v1.4 Team Foundation

## 1. Spec 文档落地(必须先做 — 后面所有实现按 spec 来)

- [x] 1.1 创建 `openspec/changes/v1-4-team-foundation/` 目录(骨架已就绪)
- [x] 1.2 写 `team-management/spec.md`(Team/Member/Message + Mailbox 文件层 +
      Lockfile 协议 + Lifecycle CLI + Registry 唯一性 + TeamsConfig)
- [x] 1.3 写 `configuration/spec.md` delta(`teams:` 块 schema + config.example
      更新)
- [x] 1.4 写 `proposal.md`(已完成)+ `design.md`(已完成)+ `tasks.md`(本文档)

## 2. Schema + 名字校验(`baozicode/teams/` 新包)

- [x] 2.1 `baozicode/teams/__init__.py` — 公开 API re-export
      (`Team` / `Member` / `Message` / `BackendType` / `TeamNameValidator` /
      `TeamsRegistry` / `TeamStore` / `Mailbox` / `mailbox_lock`)
- [x] 2.2 `baozicode/teams/schema.py` —
      `TeamNameValidator.validate(name)`(纯函数:字符集 + 长度 + 起止) +
      错误枚举(`TeamNameTooShort` / `TeamNameTooLong` / `TeamNameBadChar` /
      `TeamNameBadStart` / `TeamNameBadEnd` / `TeamNameDoubleHyphen`)
- [x] 2.3 `Team` frozen dataclass(name / lead / created_at / members /
      metadata)+ `to_json` / `from_json` / `load` / `save`,`schema_version: "1.0"`
      头
- [x] 2.4 `Member` frozen dataclass(name / role / workdir / backend /
      requires_approval / config)+ `BackendType` Pydantic `Literal` 强校验 +
      `__post_init__` 自动补 workdir
- [x] 2.5 `Message` frozen dataclass(sender / body / timestamp / read /
      summary)+ `to_json_line` / `from_dict`
- [x] 2.6 测试 `tests/test_teams_v14_schema.py` ~20 个 case
      (名字校验 8 / Team JSON 3 / Member 4 / Message 3 / BackendType 2)

## 3. Mailbox 文件层(JSONL 原子写 + 状态文件 + wake 信号)

- [x] 3.1 `baozicode/teams/mailbox.py` —
      `Mailbox.append_message(dir, direction, msg) -> None`
      - timestamp 自动补 None → `datetime.now(timezone.utc)`
      - 拿 `mailbox_lock(dir / ".lock")` 后:
        - 写临时文件 `.{direction}.jsonl.{pid}.{rand}`
        - `flush` + `fsync`
        - `shutil.copyfileobj` 追加到目标
        - 目标 `flush` + `fsync`
        - 删临时
        - release 锁
- [x] 3.2 `Mailbox.read_messages(dir, direction) -> list[Message]` —
      读整个 JSONL,跳过坏行,返回 list(默认空 list 当文件 0 字节)
- [x] 3.3 `Mailbox.read_state(dir) -> MemberState` — 读 `state.json`,
      缺字段填默认 `{status: "offline", last_active_ts: None,
      current_task: None, backend_pid: None}`
- [x] 3.4 `Mailbox.write_state(dir, state: MemberState) -> None` —
      原子写 state.json(write-then-rename)
- [x] 3.5 `Mailbox.touch_wake(dir) -> None` — `Path.touch()` 空文件,
      mtime 更新
- [x] 3.6 `Mailbox.wait_for_wake(dir, timeout: float = 30.0) -> bool` —
      异步 poll `wake.signal` mtime,200ms 间隔,返回 bool 是否触发;基础
      实现,后续 pane-backend proposal 替换 pane 端实现
- [x] 3.7 测试 `tests/test_teams_v14_mailbox.py` ~12 个 case
      (append happy 2 / multi-append 1 / atomic crash 2 / state 读默认值
      2 / state 写原子 1 / wake touch 1 / read_messages 坏行 2 /
      wait_for_wake timeout 1)

## 4. Lockfile 跨平台抽象

- [x] 4.1 `baozicode/teams/lockfile.py` —
      `MailboxLock` Protocol + `_PosixMailboxLock`(用 `fcntl.flock` +
      LOCK_EX | LOCK_NB + 50ms 退避重试 + stale mtime 偷锁)
- [x] 4.2 `_WindowsMailboxLock`(用 `msvcrt.locking(fd, LK_NBLCK, 1)` +
      `os.O_BINARY` 打开 + 同样的 stale 偷锁)
- [x] 4.3 `mailbox_lock(path, *, timeout=5.0, stale_seconds=30.0)` context
      manager 按 `sys.platform` 分发;`MailboxLockTimeout` 异常(path +
      elapsed)
- [x] 4.4 lockfile 内容写入 `{pid}\n{hostname}\n{ts}\n`(便于 debug 谁持
      锁),`os.fsync` 落盘后再返回
- [x] 4.5 测试 `tests/test_teams_v14_lockfile.py` ~10 个 case
      (POSIX happy 2 / POSIX blocking 1 / POSIX stale steal 1 / POSIX
      timeout 1 / Windows via monkeypatch 3 / context manager exception
      path 1 / cross-platform dispatch 1)

## 5. TeamStore + Registry(目录 bootstrap + 唯一性约束)

- [x] 5.1 `baozicode/teams/store.py` —
      `TeamStore.create(name, *, lead="lead") -> TeamStore` —
      `O_CREAT | O_EXCL` 创建 team 目录 + 写 `team.json`,失败抛
      `TeamAlreadyExists`
- [x] 5.2 `TeamStore.load(name) -> TeamStore` — 读已有 `team.json`,
      缺字段报错
- [x] 5.3 `TeamStore.show() -> Team` — 返回 dataclass 实例
- [x] 5.4 `TeamStore.add_member(member: Member) -> None` — 校验同名 + 建
      `<member>/` 子目录 + 写默认 `state.json`(status="offline"),
      失败抛 `MemberAlreadyExists`
- [x] 5.5 `TeamStore.destroy(*, confirm: bool = False) -> None` —
      `shutil.rmtree(team_dir)`,无 confirm 时需 CLI 显式确认
- [x] 5.6 `baozicode/teams/registry.py` —
      `TeamsRegistry.bootstrap(config: AppConfig) -> TeamsRegistry` —
      建 `teams_dir` + 扫所有 `<team>/team.json` 建索引
- [x] 5.7 `TeamsRegistry.list_teams() -> list[str]`(字典序) +
      `get(name) -> TeamStore | None`
- [x] 5.8 测试 `tests/test_teams_v14_store.py` ~12 个 case
      (create 3 / load 1 / add_member 3 / destroy 2 / registry bootstrap
      2 / 同名并发 1)

## 6. CLI 子命令(argparse)

- [x] 6.1 `baozicode/teams/cli.py` —
      `add_subcommand(subparsers)` 注册 `team` 子命令到顶层 argparse
- [x] 6.2 5 个子命令实现:
      - `create <name>` + `--scope {user|project}`(默认 user)
      - `list` + `--scope`
      - `show <name>` + `--scope` — pretty JSON 到 stdout
      - `use <name>` + `--scope` — foundation 仅打印"已激活"
      - `destroy <name>` + `--scope` + `--yes/-y` + `--force`
- [x] 6.3 错误处理:退出码 2(name 不合法)/ 3(team 不存在)/ 4(权限 /
      IO 错);所有错误走 stderr `Error: <enum>: <detail>` 格式
- [x] 6.4 `baozicode/teams/cli.py` 含 main(argv=None) 入口,
      `python -m baozicode.teams.cli` 可独立跑(用于测试)
- [x] 6.5 测试 `tests/test_teams_v14_cli.py` ~15 个 case
      (5 子命令各 happy 5 + 参数错误 5 + destroy 确认交互 3 + 退出码
      2)

## 7. 配置 schema

- [x] 7.1 `baozicode/config/schema.py` —
      `TeamsConfig` Pydantic(`dir: str = "~/.config/baozicode/teams/"`)
      + `AppConfig.teams: TeamsConfig | None = None`
- [x] 7.2 `config.example.yaml` 加 `teams:` 完整示例(注释里点明
      coordinator / pane_backend 留给后续 proposal)
- [x] 7.3 测试 `tests/test_teams_v14_config.py` ~3 个 case
      (默认 dir / 自定义 dir / 空字符串 Pydantic 拒)

## 8. App 集成

- [x] 8.1 `baozicode/app.py` —
      `BaoZiCodeApp._build_teams_registry() -> TeamsRegistry` —
      `TeamsRegistry.bootstrap(self.config)`
- [x] 8.2 `BaoZiCodeApp.on_mount` 末尾(在 sessions / commands / skills
      bootstrap 之后)调 `_build_teams_registry`,挂 `self.teams` 句柄
- [x] 8.3 `BaoZiCodeApp.on_unmount` 关 `self.teams`(无 IO 操作,仅
      释放引用)
- [x] 8.4 `baozicode/cli.py` —— `__main__.py` 顶层 argparse 加 `team` 子
      命令分发(调 `teams.cli.main`)
- [x] 8.5 测试 `tests/test_teams_v14_app.py` ~4 个 case
      (bootstrap on_mount / self.teams 是 TeamsRegistry / CLI 分发 / 无
      teams 配置走默认)

## 9. 文档

- [x] 9.1 `docs/migrations/v1.3-to-v1.4.md` ——
      迁移指南 + 新 CLI 子命令 + `teams:` 配置示例(无 breaking
      change,主要讲新能力)
- [x] 9.2 `README.md` "Team Management" 段加 `baozicode team` 示例 +
      文件布局图(`~/.config/baozicode/teams/<team>/<member>/`)
- [x] 9.3 更新 `CHANGELOG.md` 增 v1.4.0-foundation 段
- [x] 9.4 更新项目根 `CLAUDE.md` "v1.X 范围"段加 v1.4 foundation +
      `baozicode/teams/` 模块结构图 + 锁定决策清单(放在 v1.4 范围段
      内,后续 3 个 proposal 引用)

## 10. 集成测试

- [x] 10.1 `tests/integration/test_teams_v14_e2e.py` ——
      端到端:`baozicode team create devops` → `add_member(alice)` →
      写一条 message → `Mailbox.read_messages` 读到 → `destroy` →
      目录清空
- [x] 10.2 `tests/integration/test_teams_v14_lockfile_concurrency.py` ——
      两个进程并发 `append_message`,验证顺序由锁决定、无丢失无交错
- [x] 10.3 `tests/integration/test_teams_v14_cli_e2e.py` ——
      `subprocess.run(["python", "-m", "baozicode", "team", "create", ...])`
      端到端,验证退出码 / stdout / stderr / 目录结构

## 11. Review + Release

- [x] 11.1 全量 `pytest tests/ -v` 通过(~1500+ 个) — 1503 passed, 3 skipped
- [x] 11.2 全量 OpenSpec 校验通过
      (`openspec validate v1-4-team-foundation`)
- [x] 11.3 CHANGELOG + CLAUDE.md + README + config.example.yaml 同步
- [x] 11.4 `git commit -m "feat(v1.4-foundation): team data layer + mailbox + lockfile + CLI"`
      — `c301106` (32 files, +6520/-4)