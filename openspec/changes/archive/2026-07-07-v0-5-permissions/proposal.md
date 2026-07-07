## Why

v0.2 引入的 `Permissions` 是个二元白/黑名单模型,过于简陋。三类核心缺口让权限形同虚设或反咬一口:**(1)** `deny` 列表只能拦 `Bash*` 这种模糊 glob,挡不住 `rm -rf /` / `sudo` / `:(){ :|:& };:` 这类硬危险命令,而且规则模式与 `auto_allow` 在同一层互不区分优先级;**(2)** Read / Write / Edit 完全没做路径边界检查,模型可以读 `/etc/passwd`、写 `~/.ssh/authorized_keys`;**(3)** 用户被拒后 `DENIALS_EXCEEDED` 直接终止 Agent Loop,模型没机会从错误里调整策略。Claude Code / Cursor 等同类工具已经普及五层防御 + 三档信任 + 本次/会话/永久放行,BaoZiCode 这一层能力空白已经大到影响产品定位。

## What Changes

- **新增 `baozicode/permissions/` 模块**,实现五层防御流水线:`L1 硬拦截黑名单` → `L2 路径沙箱` → `L3 规则引擎` → `L4 权限模式` → `L5 人在回路`
- **新增三层 YAML 配置**:`~/.config/baozicode/permissions.yaml`(user global)→ `<project>/.baozicode/permissions.yaml`(project,进 git)→ `<project>/.baozicode/permissions.local.yaml`(local,推荐 .gitignore)
- **规则匹配**:每条规则 `ToolName(pattern) → allow | deny`,`pattern` 仅支持 fnmatch glob;**判断语义** = deny 一票否决 + 离 call 最近的 allow 胜出(session > local > project > user)
- **`PermissionModal` 升级**:三个放行档位 — 仅本次 / 本会话(写入内存 `_session_rules`)/ 永久(去重追加到 `permissions.local.yaml`)
- **`/permissions mode` 斜杠命令**:切换 `strict` / `default` / `permissive` 三档权限模式(permissive 跳过 L5,只走 L1+L2)
- **L1 黑名单**:纵深防御,文本层扫 raw command/path 后再用 shlex 解析逐 token 判断;硬编码在 `permissions/blacklist.py`,**不可被任何配置放行**
- **L2 路径沙箱**:`project_root.resolve()`(消除 symlink);Read/Write/Edit 检查 `file_path`,Bash 用正则抽出命令里的路径字面量后逐个 resolve 检查
- **拒绝后不终止 Agent Loop**:移除 `DENIALS_EXCEEDED` 停止条件,改为软警告 — 同 tool_name 连拒 ≥ 5 次(可配)注入 `<system-reminder>`;同 `call_id` 重试立即再弹 Modal;保留 `FAILED_TOOL_LOOP` 防卡死
- **`Permissions` schema 扩展**:`mode: Literal["strict","default","permissive"]` 字段;新结构与 v0.2 旧字段(`auto_allow` / `deny`)并存,旧字段被新 `rules:` 块覆盖时给出 deprecation 提示
- **向后兼容**:未配置新权限文件时退回 v0.2 行为(`auto_allow` 跳 Modal,`deny` 软拒绝);零配置文件升级,旧用户无感

**BREAKING**:
- 移除 `DENIALS_EXCEEDED` 停止条件(行为:同工具连续被拒不再终止 Agent)
- `Permissions.deny` 语义从"软拒绝 + 仍执行"变为"硬拒绝(L1/L2/L3 命中)或规则级 deny(L3)"
- 旧的 `PermissionModal` `allow-all` 按钮(`batch_apply_all`)被新三档放行取代;`batch_confirm` 字段保留(v0.2 行为降级为"会话级放行")

## Capabilities

### New Capabilities
- `permissions`: 五层防御的总能力 — `PermissionDecision` 数据结构、`permissions.check(call)` 流水线接口、三层 YAML 加载与合并、规则格式 `ToolName(pattern) → allow | deny`、deny 一票否决语义
- `dangerous-command-blacklist`: L1 硬拦截的具体规则 — 文本层正则集合(rm -rf / · sudo .* · chmod -R 7 · 常见 fork 炸弹 · curl | sh · dd if= 等)+ shlex token 解析层 + "不可被配置放行"的实现保证
- `path-sandbox`: L2 路径边界的具体规则 — `project_root` 解析为 real path、Read/Write/Edit/Bash 各自路径参数提取、resolve symlink 后 is_relative_to 判断、跨边界越界路径列表回传

### Modified Capabilities
- `tool-calling`: 拒绝语义重构(从"软拒绝 + DENIALS_EXCEEDED 终止"改为"L3 deny 硬拒绝 + 软警告注入,LLM 继续调整策略");`execute_tool_call` 调用前先经 `permissions.check`
- `interactive-tui`: `PermissionModal` 按钮组改为 `[Y 仅本次] [A 本会话] [P 永久] [N 拒绝]`;新增 `/permissions mode` 斜杠命令;`/status` 输出新增 mode 字段;`/permissions` 输出展示五层状态摘要
- `configuration`: `Permissions` 块扩展为新结构 `PermissionsV5{mode, rules}`;新增加载顺序 `local > project > user-global`;新增 `<project>/.baozicode/permissions*.yaml` 路径解析;配置文件缺失时 silent fallback 到 v0.2 行为

## Impact

**新增模块**:`baozicode/permissions/`(`__init__.py`, `types.py`, `blacklist.py`, `sandbox.py`, `engine.py`, `mode.py`, `loader.py`, `persistence.py`)

**修改文件**:
- `baozicode/agent/loop.py` — `executor` 闭包内的 `_matches_deny` / `_is_auto_allowed` 替换为单次 `permissions.check(call)` 调用;`record_denial` / `check_deny_threshold` 适配软警告(改名为 `record_denial_warn`);移除 `DENIALS_EXCEEDED` 分支
- `baozicode/agent/guards.py` — `record_denial` 改为不增触发终止的计数器,新增 `should_inject_denial_reminder(tool_name, threshold=5)` 判定;`check_deny_threshold` 降级为 `check_denial_reminder_threshold`
- `baozicode/agent/events.py` — 移除 `StopReason.DENIALS_EXCEEDED` 枚举值(或保留但不在 Agent 主循环中触发)
- `baozicode/tools/base.py` — `ToolDefinition` 新增 `path_args: list[str]` 字段(声明哪些参数是路径),供 L2 PathSandbox 用
- `baozicode/tools/{read,write,edit}.py` — `TOOL` 填 `path_args=["file_path"]`
- `baozicode/tools/bash.py` — `TOOL` 填 `path_args=[]`(由 L2 内部用正则抽取),`BashSession._is_inside` 复用 `PathSandbox.is_inside`
- `baozicode/config/schema.py` — 新增 `PermissionRule` / `PermissionMode` / `PermissionsV5`;`Permissions` 保留旧字段 + `mode` + `rules`;`active_permissions_v5()` 方法
- `baozicode/config/loader.py` — 新增 `load_permissions_layers(project_root) -> MergedPermissions`
- `baozicode/app.py` — 启动时调 `permissions.bootstrap(project_root, config)` 加载合并后的配置
- `baozicode/tui/permission_modal.py` — 按钮组重构;返回 `PermissionChoice` 枚举(`ONCE / SESSION / PERSISTENT / DENY`)
- `baozicode/tui/chat_screen.py` — `_permission_callback` 适配新返回类型;新增 `_handle_permissions_mode` 命令;`_show_permissions` / `_show_status` 渲染新字段
- `config.example.yaml` — `permissions:` 块补全 v0.5 字段示例 + 注释三层 YAML 路径

**新增测试**:
- `tests/test_permissions_blacklist.py` — L1 文本层 + shlex 层命中/漏判样例
- `tests/test_permissions_sandbox.py` — L2 symlink 逃逸 / 相对路径 / `..` 逃逸 / 合法路径放行
- `tests/test_permissions_engine.py` — L3 三层 YAML 合并 / 同 key 优先级 / deny 短路 / allow 短路
- `tests/test_permissions_mode.py` — L4 strict 拒 / permissive 自动过 / default 落到 L5
- `tests/test_permissions_integration.py` — Agent.executor 集成测试,验证 deny 喂回 LLM + 同 call 重试弹 Modal
- `tests/test_permission_modal.py` — 三档放行选择 + 持久化落盘验证

**依赖**:无新增(标准库 `shlex` / `fnmatch` / `pathlib` / `re` 已够用)。

**配置文件**:`permissions.local.yaml` 不进 git;`README` 提示在 `<project>/.gitignore` 加 `.baozicode/permissions.local.yaml`。

**CLAUDE.md**:模块结构图加 `baozicode/permissions/`;依赖方向加 `permissions/ → config/ + tools/base.py`;关键约定章节加五层防御契约。