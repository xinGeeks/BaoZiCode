# v1.4 Team Lead — Foundation Proposal

## Why

v1.2 + v1.3 把 sub-Agent 和 git worktree 隔离做完了,但 Lead Agent 仍然是
**「单 Agent 自己做所有事」**:即便派多个 sub-Agent,Lead 也是做完一个再做
下一个,队员之间没有横向通信,所有消息都得经过 Lead 中转。

v1.4 的目标是把 Lead 升级为 **Team Lead**,让它:

- 把用户目标拆成一组**带依赖关系的任务**,写进**共享任务清单**
- 派生多个**长期存在的队员**,每个队员独立工作目录、独立后端
- 队员之间通过**共享邮箱**直接通信(不绕 Lead)
- 队员干完通知 Lead → Lead 派下一个任务 / 合并成果
- 全部完成后 Lead 用 `git merge` 合各队员的目录,冲突回滚上报

这是一个**分四个独立 proposal 推进**的大版本。本 proposal(`v1-4-team-foundation`)
只做**数据层**——把"团队是什么、消息怎么落盘、锁怎么拿"这层先建好;后续三
个 proposal(`team-tools` / `team-pane-backend` / `team-coordinator`)在此
之上加协作工具、pane 后端、coordinator 模式。

**为什么分四步**:

| Proposal        | 职责                                         | 依赖                |
|-----------------|----------------------------------------------|---------------------|
| **foundation**  | Team / Member / Message 数据 + mailbox 文件 + 锁 | 无                  |
| team-tools      | 协作工具(`team_dispatch` / `team_send_message` / `team_merge` / `team_cancel`) | foundation          |
| team-pane-backend | tmux / iTerm2 / Windows Terminal + pane wake + watchdog resume | foundation          |
| team-coordinator | 配置 + env var 两把锁 + 工具收缩 + `team_merge` 一等公民 | foundation + team-tools |

四个独立 proposal 互相正交,任何一个失败 / 回滚都不影响其他三个。foundation
落地后即使用户不用团队协作,数据层也是有用的(可以手动读 mailbox 文件
debug)。

## What Changes

新增 1 个能力模块 + 改 1 处配置契约:

- **新增 `baozicode/teams/`** —— 顶层包,跟 `agents/` / `worktree/` 平级。4
  个子模块 + 1 个 CLI 子包:
  - `schema.py` —— `Team` / `Member` / `Message` dataclass + `MailboxLock`
    协议 + 错误枚举 + 名字校验
  - `mailbox.py` —— 邮箱文件读写(JSONL 原子写 + wake 信号文件 + 状态文件)
  - `registry.py` —— Team / Member 名称注册表 + 唯一性约束
  - `store.py` —— Team 目录 bootstrap(create / load / persist / destroy)
  - `cli.py` —— argparse 子命令(`team create / list / use / show / destroy`)
- **新增 capability spec** `team-management` —— Team 数据模型 + Mailbox 文
  件格式 + Lockfile 协议 + Lifecycle CLI
- **MODIFIED** `configuration` spec —— `AppConfig` 加 `teams: TeamsConfig |
  None` 块:`dir`(默认 `~/.config/baozicode/teams/`),其他字段(coordinator
  能力开关等)留给 `v1-4-team-coordinator` proposal
- **MODIFIED** `BaoZiCodeApp` —— `_build_teams_registry()` 单例;`on_mount`
  阶段调 `teams.bootstrap(config)`,挂 `self.teams: TeamsRegistry` 句柄
- **MODIFIED** `cli.py` —— argparse 加 `team` 子命令,无破坏性

## Out of Scope(后续 3 个独立 proposal)

- **team-tools** —— `team_dispatch` / `team_send_message` / `team_cancel` /
  `team_merge` 工具实现(只在 team 内成员的 Agent Loop 可见,主 Agent / 普
  通 sub-Agent 看不到)
- **team-pane-backend** —— tmux / iTerm2 / Windows Terminal pane 创建 +
  watchdog 协程 + pane wake 信号触发
- **team-coordinator** —— coordinator 能力开关(`teams.coordinator.enabled`)
  + `BAOZICODE_COORDINATOR` env var 两把锁 + 工具收缩 + `team_merge` 升
  一等公民

不在 v1.4 范围(永远不做):

- 跨机器分布式团队
- 成员间实时流式通信(只走 mailbox 文件 + 轮询)
- 复杂任务依赖约束(DAG 字段只支持简单的 `depends_on: list[str]`,不支持条
  件依赖 / 优先级 / 资源约束)

## Capabilities

### New Capabilities

- **`team-management`** —— Team / Member / Message 数据模型,mailbox 文件
  格式(JSONL + wake.signal + state.json),lockfile 跨平台抽象,生命周期
  CLI(create / list / use / show / destroy),TeamsConfig 配置块

### Modified Capabilities

- **`configuration`** —— `AppConfig` 加 `teams: TeamsConfig | None` 块;
  `TeamsConfig.dir` 字段,默认 `~/.config/baozicode/teams/`。其他
  coordinator / pane_backend 子块不在本 proposal 范围

## Impact

**代码**(`baozicode/`):

- `teams/__init__.py`(新) —— 公开 API re-export
- `teams/schema.py`(新) —— `Team` / `Member` / `Message` dataclass + `Role` /
  `BackendType` 枚举 + 错误枚举 + `TeamNameValidator.validate(name)`
- `teams/mailbox.py`(新) —— `Mailbox.append_message()` / `read_messages()` /
  `touch_wake()` / `read_state()` / `write_state()`;JSONL 原子写(write-then-
  rename)+ wake 信号文件 + state.json 状态文件
- `teams/lockfile.py`(新) —— `MailboxLock` 协议 + `_PosixMailboxLock`(用
  `fcntl.flock`) + `_WindowsMailboxLock`(用 `msvcrt.locking`)+ 跨平台
  分发 + stale 锁回收(默认 30s)
- `teams/registry.py`(新) —— `TeamsRegistry`:`teams_dir` 下所有 team 索引
  + 名称唯一性约束(create 时校验)
- `teams/store.py`(新) —— `TeamStore.create(name)` / `load(name)` /
  `list()` / `show(name)` / `destroy(name, *, confirm)`
- `teams/cli.py`(新) —— `team` 子命令 argparse(`create / list / use / show /
  destroy`)
- `cli.py`(改) —— `__main__.py` 顶层 argparse 加 `team` 子命令分发
- `config/schema.py`(改) —— `TeamsConfig` Pydantic + `AppConfig.teams`
- `app.py`(改) —— `_build_teams_registry()` + `on_mount` 末尾 bootstrap
- `tui/chat_screen.py`(改) —— 不改 TUI 布局;foundation 不引入任何 TUI
  变化(后续 team-tools proposal 才加 team 状态条)

**依赖方向**:

```
teams/  →  config/schema.py          (读 TeamsConfig)
       →  tools/base.py             (无直接依赖,纯文件层)
       ↓
       ←  agent/  (后续 proposal 依赖)
       ←  tui/    (后续 proposal 依赖)
```

`teams/` 不依赖 `agent/` / `tui/` / `llm/` / `permissions/` —— 纯数据层,
可独立单元测试。

**测试**(`tests/test_teams_v14.py` 等新文件):

- `TeamNameValidator`:`~6 个` —— 合法 / 空 / 太长 / 大写 / 特殊字符 / 起点
  连字符
- `Team / Member / Message dataclass`:`~4 个` —— 字段必填 / frozen / 默
  认值 / JSON round-trip
- `Mailbox 文件读写`:`~5 个` —— inbox append / outbox append / atomic
  write(写一半崩不破坏)/ wake touch / state 读
- `Lockfile`:`~6 个` —— POSIX 拿锁 / 拿不到重试 / stale 锁回收 / Windows
  msvcrt / 跨平台分发 / context manager 异常路径
- `TeamStore`:`~6 个` —— create 目录结构 / load / list / destroy 确认 /
  同名创建报错 / destroy 不存在报错
- `Registry 唯一性`:`~3 个` —— 同名 team 并发 create 只一个成功 / 同
  team 内同名 member 报错
- `CLI 子命令`:`~10 个` —— argparse 5 个子命令各 happy / 参数错误 / 缺
  少参数
- `TeamsConfig`:`~3 个` —— 缺省默认 / dir 自定义 / 不合法路径报错

总计约 `~45 个` 新测试。

**文档**:

- `openspec/specs/team-management/spec.md`(新)—— 7–9 个 Requirement(
  Team 数据模型 / Member 数据模型 / Message 格式 / Mailbox 文件布局 /
  Lockfile 协议 / Lifecycle / Registry / CLI / TeamsConfig)
- `openspec/specs/configuration/spec.md`(改)—— 加 1 个 requirement
  (`teams:` 块 schema + 缺省默认)
- `docs/migrations/v1.3-to-v1.4.md`(新)—— 迁移指南 + 新 CLI + 配置示例
- `README.md` 加一段 "Team Management" 介绍 + CLI 示例
- `config.example.yaml` 加 `teams:` 完整示例
- 项目根 `CLAUDE.md` "v1.X 范围"段加 v1.4 foundation

**可能的 breaking change**(评估):

- 本 proposal 无任何破坏性改动 —— 全部新增:
  - 新模块 `baozicode/teams/` 不影响现有任何模块
  - `AppConfig.teams` 是 optional 字段,缺省走 bootstrap 默认
  - CLI 加 `team` 子命令,不与现有子命令冲突
  - v1.3 builtin agents / 已存在 sessions 不受影响

## Risks

[R1] **lockfile 跨平台不一致** —— POSIX 用 `fcntl.flock`,Windows 用
`msvcrt.locking`,两套语义略不同(flock 是 advisory / locking 是 mandatory)。
Mitigation:把 `MailboxLock` 抽成 Protocol,跨平台分发工厂
`_make_lock(path)` 按 `sys.platform` 选实现;stale 锁默认 30s 超时(两平台
都按 mtime 判);写测试覆盖两个平台分支(monkeypatch `sys.platform`)。

[R2] **JSONL 写一半崩破坏文件** —— `Mailbox.append_message` 必须原子写。
Mitigation:写 `tmp.jsonl.<random>` + `os.replace(tmp, target)`(POSIX
原子;Windows 上 `os.replace` 也原子,因为同卷);flush + fsync 后再 replace。
测试覆盖"模拟写到一半 kill -9 后重启,文件仍是合法 JSONL"。

[R3] **同 team 同名 member** —— alice 被删后重建,旧 mailbox 残留。
Mitigation:create member 时校验唯一性;destroy member 默认保留 30 天软删
除(`state.json.marked_for_removal=true`),后台清理回收。也允许
`destroy(name, force=True)` 立即删。

[R4] **teams_dir 跨用户/项目混用** —— `~/.config/baozicode/teams/` 是全
局共享,但 team 内的 worktree 路径是项目本地。Mitigation:`team create` /
`destroy` 加 `--scope` 显式区分(默认 user;`--project` 走
`<project>/.baozicode/teams/`);foundation 只实现 user 范围,project 范围
留 `team-tools` proposal。

[R5] **CLI 误删 team** —— `team destroy <name>` 不小心删了带队员上下文
的 team。Mitigation:默认 `team destroy` 必须加 `--yes` 才执行(像
`rm -rf` 的 `--interactive`);不带 `--yes` → 交互确认;`--force` 跳过确认
+ 强删;CLI 测试覆盖三种路径。

[R6] **Team / Member 字段后续扩展困难** —— schema 现在定的字段会影响所
有持久化文件。Mitigation:frozen dataclass + JSON 序列化时多余字段忽略
(`__init__` 默认值兼容旧文件);字段重命名走 `__old_field__ → new_field`
迁移 helper(类似 v0.7 sessions 的 uuid 迁移)。

## Migration Plan

无用户可见 breaking change。foundation 默认行为是「啥也不做」:`teams/`
目录不创建、`teams:` 配置缺省走默认、`team` 子命令不被自动调用。

部署:
1. 合并 `v1-4-team-foundation` → main
2. bump version 1.3.0 → 1.4.0
3. release notes:
   - "新增 `baozicode team` CLI 子命令:create / list / use / show / destroy"
   - "新增 `teams:` 配置块(可选,缺省 `~/.config/baozicode/teams/`)"
   - "新增 capability `team-management`:Team 数据 + mailbox 文件 + 锁
     协议"

回滚:
- 单 commit revert
- `teams/` 模块不影响现有任何模块,revert 后行为完全等价 v1.3
- 用户已创建的 team 目录保留(只是 CLI 找不到入口),不会自动清理