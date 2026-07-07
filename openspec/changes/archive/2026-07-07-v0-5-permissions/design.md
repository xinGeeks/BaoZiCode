## Context

v0.2 的 `Permissions` 设计是 LLM 工具调用时代早期产物:**只有"自动放行名单"和"黑名单"两个 list**,`PermissionModal` 只有 Y/N 三个按钮,`Bash` 工具内嵌 `cwd` 状态机做软边界。v0.3 把 Agent 抽出独立模块,加了 5 种停止条件和并发调度,但**没有碰权限核心** —— 只是把 v0.2 的 `PermissionModal` 调用从 TUI 内联循环挪到 `Agent.permission_callback` 钩子上。

权限模型在 v0.4 之后出现三类实质缺口:

1. **危险命令可以绕过**:v0.2 的 `deny` 列表只能配 `Bash*` 这种工具级 glob,无法拦截 `rm -rf /` / `sudo` / `curl | sh` / fork bomb 这类**已知硬危险命令**。而且只要该工具名进了 `auto_allow`,所有 `deny` 规则失效。
2. **路径边界是 Bash 单点防御**:Read / Write / Edit 完全没做路径检查 —— LLM 可以读 `/etc/passwd`、写 `~/.ssh/authorized_keys`、读 `~/.bash_history`。Bash 的 `cwd` 状态机防 `cd ../../../` 逃逸,但不防 symlink(`ln -s /etc tmp; cd tmp` 后所有命令都跑在 `/etc` 上下文)。
3. **拒绝 → 终止 → 模型没机会调整**:`DENIALS_EXCEEDED` 阈值是 v0.3 为防 LLM 死循环设置的兜底,但用户体验过激 —— 模型想"绕一下"也不行。

同期竞品(Claude Code、Cursor、Aider)已经把"五层防御 + 三档信任 + 本次/会话/永久放行"做成标配。BaoZiCode 想要不在这条能力线上掉队,需要在 v0.5 一次性把这些能力补齐,但又不破坏 v0.4 的依赖方向和模块边界。

**约束**(继承自现有 CLAUDE.md):
- `baozicode/permissions/` 必须单向依赖,不能反向 import agent/tui/conversation/llm
- `LLMClient.stream` 签名不变(已是 v0.4 落地形态)
- `ToolDefinition` / `ToolCall` / `ToolResult` 数据模型不破坏(只能新增字段)
- Agent Loop 5 种停止条件保留,但**`DENIALS_EXCEEDED` 退出活跃触发** — 仅作为枚举值保留供测试和文档引用

**Stakeholders**:
- TUI 用户(`ChatScreen`): 需要新三档 Modal、模式切换命令、状态栏显示当前 mode
- 配置文件维护者: 需要清晰的 YAML schema,三层 YAML 路径有 README 引导
- 安全意识强的开发者: 需要可证明的"硬拦截不被配置放行"保证
- LLM 模型本身: 需要 deny 后 `is_error=True` + 解释性 `content` 回灌,才能调整策略

## Goals / Non-Goals

**Goals:**
1. **5 层防御纵深**:`L1 硬拦截黑名单` → `L2 路径沙箱` → `L3 规则引擎` → `L4 权限模式` → `L5 人在回路`,每层独立判定,短路输出 `PermissionDecision`
2. **硬拦截不可被配置覆盖**:L1 命中永远 DENY,任何 YAML 配置 / session rule / 用户 Modal 选择都不能放行
3. **路径沙箱防 symlink 逃逸**:`project_root.resolve()` 走文件系统消除 symlink,Bash 命令里抽路径字面量逐个 resolve
4. **三层 YAML 合并 + 短路径优先**:user < project < local < session(运行时内存);deny 一票否决,allow 取离 call 最近的层
5. **人在回路三档放行**:仅本次 / 本会话 / 永久,持久化时去重追加到 `permissions.local.yaml`
6. **拒绝后不终止 Agent Loop**:`is_error=True` 喂回 LLM;同 tool 连拒 ≥ 5 注入 `<system-reminder>` 软警告;同 `call_id` 重试立即重弹 Modal
7. **向后兼容**:零配置文件升级,旧 `Permissions.auto_allow` / `deny` 字段继续生效,v0.5 新结构存在时优先用新结构

**Non-Goals:**
1. **不做沙箱化执行**:Bash 不切到 Docker / gVisor / bubblewrap 子沙箱。沙箱是路径边界,不是进程隔离。
2. **不做 LLM 工具调用的"训练时约束"**:不在 system prompt 里教 LLM 自我审查,运行时拦截才是安全保证。
3. **不做"全团队策略分发"**:YAML 不从中心服务器拉,本地三文件足以。团队策略通过 git commit 共享。
4. **不做完整 audit log**:虽然每次决策都产 `PermissionDecision(reason)`,但不持久化到独立 audit 文件。给 LLM 回灌即可。
5. **不做 Rule DSL 升级**:fnmatch glob 一种语法,不支持正则 / 复杂表达式。YAGNI。

## Decisions

### D1: 五层防御是单次流水线调用而非"中间件栈"

**选择**: `permissions.check(call, ctx) -> PermissionDecision` 是一个函数,从 L1 到 L5 按顺序短路判断,任意层返回 ALLOW/DENY 就立即返回。

**备选 A**:每层独立组件,Agent 顺序调用 5 个 `await` — 代码更分散,难以保证短路语义
**备选 B**:Pipeline 模式,中间件链式 — 过度抽象,Pythonic 风格不需要
**理由**:单函数最易读、最易测试,流水线本质就是循环 + 短路,无需引入额外模式。`PermissionCallback` 钩子保留给 L5 Modal(异步必需),其他层都是同步。

### D2: L1 黑名单纵深防御 = 文本层 + shlex 解析层

**选择**: 文本层先做快路径正则(`rm -rf /` / `sudo\s+\S` / `chmod\s+-R\s+7`),命中即拒;未命中但命令含"危险关键词"`(rm|sudo|chmod|dd|curl|wget|nc|...)` 时,再走 shlex 拆 argv + 逐 token 判断。

**备选 A**:只做文本正则 — 简单但 echo 'rm -rf /' 也会被误伤
**备选 B**:只做 shlex 解析 — 精确但 fork bomb / `:(){ :|:& };:` 这种 shell builtin 解析不完整,且性能差
**理由**:纵深防御兼顾速度 + 精度。文本层 0.01ms 挡掉大部分情况,shlex 层只在"可疑"时启动。文本层用 compiled regex,shlex 层用 stdlib `shlex.split`。

### D3: L1 黑名单硬编码在代码里,不放进任何 YAML

**选择**: `permissions/blacklist.py` 维护两个常量:
- `TEXT_PATTERNS: list[re.Pattern]` — 文本层正则集合
- `DANGEROUS_TOKENS: set[str]` — shlex 解析后的危险 argv[0] / flag 集合(如 `{rm, sudo, dd, mkfs, ...}`)

**备选 A**:放进 user global YAML,允许用户扩展 — 但这与"硬拦截"语义矛盾,用户可以关掉,失去"硬"的意义
**备选 B**:放进 `permissions.local.yaml` — 同样问题,且 local 是用户能改的
**理由**:硬编码 = 任何配置层 / session rule / Modal 选择都不能放行,这正是"硬"的语义。**未来如果要扩展黑名单,只能改代码 + 发新版本**,这与"安全默认值"对齐。

### D4: L2 路径沙箱 resolve symlink 后用 is_relative_to 判断

**选择**: `PathSandbox` 持有 `real_root: Path`(启动时 `project_root.resolve()`,走文件系统解析所有 symlink)。每个 path 检查:`target_real = target.resolve(); target_real.is_relative_to(real_root)`。

**备选 A**:字符串前缀比较(`str(path).startswith(str(root))`)— 不防 `..` 边界、防不了 symlink 指向 `/etc/passwd` 的子路径
**备选 B**:trust 真实 root 不 resolve — 攻击者 `ln -s /etc project; baozicode project` 后所有路径检查失效
**理由**:`Path.resolve()` 是 stdlib,语义清晰。symlink 防御必须走文件系统查询(`strict=False` 容忍中间不存在),不能纯字符串。

### D5: Bash 命令里抽路径用保守正则,不依赖 shell AST

**选择**: 用正则 `r'(?:^|[\s|&;>])(~?/[^\s|&;>\']+|\.{0,2}/[^\s|&;>\']+)'` 抽出所有"看起来像路径"的字面量,逐个 resolve 检查。

**备选 A**:起一个 `bash -n` AST parser — 引入外部依赖,且对 `eval` / `bash -c` 无效
**备选 B**:完全不查,只查 cwd — 现有 v0.2 行为,被否决(已知缺口)
**理由**:保守正则漏判的边界 case(变量展开 `cp $HOME/x /tmp/y`)由 L4 permissive 模式兜底,**默认 strict/default 模式下用户通过 Modal 决定**。完美覆盖不现实,但纵深防御足以把"明确越界"的命令挡掉。

### D6: L3 规则用 fnmatch glob 唯一语法,deny 一票否决

**选择**: 规则格式 `ToolName(pattern) → allow | deny`,`pattern` 仅 fnmatch glob。三层 YAML 合并顺序:session > local > project > user-global。判断算法:

```
for layer in [session, local, project, user_global]:
    for rule in layer.rules:
        if rule.tool == call.name and fnmatch.call_args(call, rule.pattern):
            if rule.decision == "deny":
                return DENY(layer, rule.pattern)   # 短路
            elif rule.decision == "allow":
                last_allow = (layer, rule.pattern)
return last_allow or fallthrough
```

即:任一层 deny 命中 → DENY;否则用最高优先层的 allow(若没有 → fallthrough)。

**备选 A**:deny 与 allow 都逐层判断,取最高层结果 — 与 sudoers 类似,但语义不直观
**备选 B**:用户级 deny 永远赢,项目级 allow 不能覆盖 — 严格但丧失项目级 override 的能力
**理由**:D6A 跟用户给的描述"逐层判断"一致;D6B 是"deny 一票否决",跟"deny 不可被 allow 覆盖"直觉一致。**最终 = 任一层 deny 短路,否则取最高层 allow**。这种语义是大多数安全工程师熟悉的(类似 AWS IAM explicit deny)。

### D7: L4 模式仅控制 fallthrough 行为,不改已有规则

**选择**: `strict` = fallthrough 时 DENY(连 Modal 都不弹);`default` = fallthrough → L5;`permissive` = fallthrough 时 ALLOW(自动过)。模式不影响 L1/L2/L3 已有的规则判定结果。

**备选 A**:strict 也弹 Modal 让用户决定 — 与 strict 语义("少打扰、严把关")不符
**备选 B**:permissive 也跳过 L1/L2 — 极度危险
**理由**:L1/L2 是硬拦截,模式不能放大攻击面;L3 是显式配置,模式不改用户意志;模式只影响"没规则覆盖的灰色地带"。

### D8: L5 Modal 三档放行 = once / session / persistent

**选择**: Modal 按钮组:`[Y 仅本次] [A 本会话] [P 永久] [N 拒绝]`。scope 实现:
- `once`:仅当前 ToolCall 的临时变量,执行后丢弃
- `session`:追加到 `Agent._session_rules`(内存),pattern 由"first token + ` *`"生成(如 `npm test --coverage` → `npm test *`),不存精确命令串
- `persistent`:追加到 `<project>/.baozicode/permissions.local.yaml`,去重键 = `(tool, pattern, decision)`,pattern 同 session 用 glob

**备选 A**:Modal 直接让用户编辑 rules YAML — UX 太重,与 TUI 风格不符
**备选 B**:session 也落盘,加 `expires_at` 字段 — 边界模糊,且需要后台清理
**备选 C**:pattern 存精确命令串(`npm test --coverage`)— 一次只放行当前 call 的精确形式,后续 `npm test` 还要再弹。Glob 形式(`npm test *`)一次放行一类,**经验上更有用**。
**理由**:三档粒度符合用户给出的语义;去重键保证多次"永久允许"同一规则不会产生重复行;glob pattern 符合用户"放行一类命令"的直觉。

### D9: 拒绝后不终止 Loop,但保留软警告 + 重试提示

**选择**:
- 移除 `DENIALS_EXCEEDED` 触发路径(枚举保留供向后兼容)
- 同 tool_name 在 `GuardState` 里累计 `consecutive_denials`,达 `denial_warn_threshold`(默认 5)就通过 `<system-reminder type="denial_rate_limit">` 注入提醒
- 同 `call.id` 同 `arguments` 第二次出现,直接再弹 Modal("这工具之前被拒过,确认要再试吗?")而不是默默重发

**备选 A**:完全无任何反馈,模型自己悟 — 容易被"模型卡死反复调同一工具"拖死
**备选 B**:硬终止同 tool 连拒 ≥ N — 与用户需求矛盾
**理由**:软警告 = 提醒模型自己调整 + 给用户感知;"再弹 Modal" = 防止模型"忘了被拒"无脑重试。两者结合把"不终止"和"防卡死"同时满足。

### D10: 向后兼容 — 新结构优先,旧字段降级

**选择**:
- `PermissionsV5{mode, rules}` 是新结构;`Permissions.auto_allow / deny / batch_confirm` 是旧字段
- `active_permissions_v5()` 检测:若新 YAML 文件存在(`local > project > user-global` 任一) → 用新结构;否则把旧字段转成等价的 v0.5 rule(`auto_allow` 转 `allow` rules,`deny` 转 `deny` rules,`batch_confirm` 转 session 行为)
- 旧 `PermissionModal.batch_apply` 按钮在新结构下 = `session` 档

**备选 A**:完全抛弃旧字段,要求所有用户升级 YAML — 破坏 v0.4 用户
**备选 B**:新结构只在用户显式启用时生效(开关) — 增加配置负担
**理由**:YAML 文件存在与否是天然的开关 — 升级到 v0.5 但不动权限的用户,行为与 v0.4 完全一致。

## Risks / Trade-offs

### R1: L2 路径沙箱在 Bash 上漏判变量展开

**Risk**: 命令 `cp $HOME/secret /tmp/x` 中 `$HOME` 不会被正则抽出,L2 看到的是字面量 `$HOME/secret` 被误判为合法路径(因为 `$HOME/secret` 不在 project_root 下)。

**Mitigation**: 检测到 `$VAR` / `${VAR}` / `~` / `` `cmd` `` 等"会扩展"的路径标记时,直接 DENY(reason = "shell expansion in path"),把决定权交给 L5 Modal。这是**保守优于漏判**的取舍。

### R2: L1 黑名单误伤合法命令

**Risk**: `sudo apt update` 命中 L1(因为 `sudo \S+` 模式)→ 用户每次升级 apt 都要走 L5。

**Mitigation**: L1 文本模式只覆盖**真正危险**的命令,例如 `sudo\s+(rm|chmod|chown|mv|cp)` 而非所有 `sudo`。shlex 层对 `sudo <known-safe-bin>` 加白名单(`apt` / `systemctl` / `docker`)单独豁免。**这条白名单也是硬编码**,不可配置。

### R3: 三层 YAML 加载顺序与 `config.yaml` 加载顺序混淆

**Risk**: 用户习惯于 `config.yaml` 是单文件,v0.5 引入三个 `permissions*.yaml` 容易配错路径或搞混优先级。

**Mitigation**: 启动时若检测到 project 目录有 `.baozicode/` 但没有 `permissions.yaml`,打印 info 级提示("未找到权限配置,使用默认 strict 模式");`/status` 命令展示当前三层 YAML 的解析状态和来源;`README` 在配置章节加完整示例。

### R4: 永久放行写入 permissions.local.yaml 时的并发/原子性

**Risk**: 用户在 Modal 选"永久",我们 `yaml.dump` + `f.write` 期间进程崩溃或用户多次快速操作,可能写入半截文件或丢失并发写入。

**Mitigation**: 用"读 → 修改 → 临时文件 → `os.replace` 原子替换"的标准 atomic write 模式;同一进程内的并发由 Python GIL + 我们的串行化操作保证(每次 Modal dismiss 后才触发下一轮 LLM)。不处理跨进程并发(同一项目目录同时跑两个 BaoZiCode 的场景假设不存在)。

### R5: 软警告阈值 5 是拍脑袋定的

**Risk**: 默认 5 次拒绝后注入提醒,可能太频繁(用户每次都要看 `<system-reminder>`)或太少(模型已经卡死 5 次才提醒)。

**Mitigation**: 阈值放进 `AppConfig.agent.denial_warn_threshold`,默认 5,可在 `config.yaml` 调整;`/status` 显示当前阈值与本会话已触发次数。

### R6: DENIALS_EXCEEDED 移除破坏依赖它的外部代码

**Risk**: 第三方脚本可能 import `StopReason.DENIALS_EXCEEDED` 或在文档里引用。

**Mitigation**: 枚举值保留,只在 Agent 主循环的停止条件判定中不触发;`openspec/specs/agent-loop/spec.md` 显式标注"DENIALS_EXCEEDED 是保留枚举,无活跃触发路径",文档指引用户改用软警告注入的新行为。

### R7: 软警告 system-reminder 与 plan_mode reminder 冲突

**Risk**: 同一次 LLM 调用中可能既有 plan_mode reminder 又有 denial_rate_limit reminder,位置争夺 `messages[-2]` 的插入点。

**Mitigation**: `_inject_reminders` 已经在 v0.4 实现"在 messages[-2] 前依次 splice" — 多个 reminder 各自是独立 Message 对象,顺序按注入列表先后,无冲突。denial reminder 紧跟 plan_mode reminder 之后,语义清晰。

## Migration Plan

**阶段 1 — 新增模块,不动现有行为**:
1. 创建 `baozicode/permissions/` 全模块,写好所有单元测试
2. `AppConfig` 新增 `permissions_v5: PermissionsV5 | None = None` 字段
3. `active_permissions_v5()` 默认返回 `None`(= 不启用新结构)
4. 此时 `Agent.loop` 完全没改,行为与 v0.4 完全一致

**阶段 2 — 接入 Agent,旧行为降级**:
1. `Agent.__init__` 增加 `permissions_v5` 参数(可选)
2. 当 `permissions_v5 is None` 时用旧 `_matches_deny` / `_is_auto_allowed` 路径(完整保留 v0.4 行为)
3. 当 `permissions_v5` 存在时走新流水线
4. `App` 启动时检测:若 `<project>/.baozicode/permissions.yaml` 存在 → 实例化 `permissions_v5`
5. 默认配置下两路径并存,旧行为自动降级

**阶段 3 — 切换 Modal 三档 + 模式切换命令**:
1. `PermissionModal` 按钮组重构为四按钮(`Y/A/P/N`)
2. `/permissions mode` 斜杠命令注册
3. `/permissions` 查看输出扩展为五层状态摘要

**阶段 4 — 移除旧路径,完成迁移**:
1. `DENIALS_EXCEEDED` 从 Agent 主循环触发链移除
2. `record_denial` / `check_deny_threshold` 重命名为软警告语义
3. `AppConfig.permissions.deny` / `auto_allow` 标 deprecated,文档引导到新 YAML

**回滚策略**:任一阶段发现严重问题 → git revert 对应 commit,旧行为完全保留(因为阶段 1 完全不破坏)。阶段 2-4 的回滚需要保证 `permissions_v5 = None` 路径仍 100% 工作。

## Open Questions

1. **L2 路径沙箱对 symlink 解析的开销**:每个 Bash 命令都要 `resolve()` 几个路径,Windows 上 symlink 处理慢。是否需要在沙箱内做缓存(同一 call 内 path 复用 resolve 结果)? — 实现阶段实测决定。

2. **Bash token 解析的边界**: `bash -c 'rm -rf /'` 用 shlex 拆 outer 是 `["bash", "-c", "rm -rf /"]`,而真正的"执行意图"是第三参数。是否需要二级递归 shlex 拆第三参数? — 实现阶段定,**保守策略 = 直接 DENY 整个 `bash -c` 命令**(理由:无法静态分析,默认拒绝最安全)。

3. **denial_warn_threshold 默认 5 是否合理**:Aider / Claude Code 默认值多少? — 实现前查证。

4. **权限模式的 YAML 来源**: `mode: permissive` 是写在 project YAML 里(共享给团队)还是 local YAML(个人覆盖)? — 倾向 project(团队共享安全策略),但 L4 mode 切换在 Modal 是会话级,如何与 YAML 里的 mode 字段协调? — 实现阶段定。

5. **同 call 重试的判定**:`call.id` 是 LLM 给的临时 ID,可能重发;但 `arguments` 完全相同 + tool_name 相同是更稳的判定键。是否需要缓存 `(tool_name, frozenset(arguments.items()))` → 上次拒绝时间? — 实现阶段定。

6. **`/permissions mode` 与 `/auto` 语义重叠**: `/auto` 是会话级全跳过,`permissive` 是 fallback 时跳过 L5 Modal。两者是否合并? — **倾向不合并,语义不同**:`/auto` 是"我不想被打扰",`permissive` 是"我信任这个项目,默认通过"。但 `/status` 显示要清晰区分。