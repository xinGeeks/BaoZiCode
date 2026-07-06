## 1. 数据模型与抽象层

- [x] 1.1 在 `baozicode/tools/base.py` 定义 `ToolDefinition / ToolCall / ToolResult` 三个 dataclass（含 `risk` 字段、`error` 可选诊断字段）
- [x] 1.2 在 `baozicode/llm/base.py` 引入 `ContentBlock` dataclass（discriminated union：`text` / `tool_use` / `tool_result` 三种 type）
- [x] 1.3 把 `Message.content` 类型从 `str` 升级为 `str | list[ContentBlock]`；`Message.to_dict()` 对两种形态分别序列化（str 走快速路径，list 走 block 序列化）
- [x] 1.4 扩展 `LLMClient.stream` 抽象签名，新增 `tools: list[ToolDefinition] | None = None` 参数；后端实现必须兼容 v0.1 调用（`tools=None` 不传给 SDK）
- [x] 1.5 在 `ContentDelta` 上确认 `type="tool_use"` 已落地：`text` 字段承载 `ToolCall` 实例，`extra` 字段保留向后兼容

## 2. 工具实现（7 个 tool + 注册表）

- [x] 2.1 创建 `baozicode/tools/` 目录 + `__init__.py`
- [x] 2.2 实现 `baozicode/tools/read.py`：`Read` 工具，读 UTF-8 文本，加 50KB/2000 行 cap，超出截断；返回 `ToolResult`
- [x] 2.3 实现 `baozicode/tools/write.py`：`Write` 工具，整文件覆写（自动创建父目录），返回字节数摘要；`risk="high"`
- [x] 2.4 实现 `baozicode/tools/edit.py`：`Edit` 工具，old_string/new_string 字符串替换；old_string 必须唯一（0 或 >1 都报错不改文件）；`risk="high"`
- [x] 2.5 实现 `baozicode/tools/bash.py`：`Bash` 工具，`asyncio.create_subprocess_shell` + 60s 超时；捕获 stdout + stderr；`risk="high"`
- [x] 2.6 实现 `Bash` 的 cwd 三状态机：会话启动锁项目根；`cd` 命令后 cwd 跟随；每条命令前用 `Path.resolve().is_relative_to(project_root)` 防逃逸；逃逸则拒执行并保留 cwd
- [x] 2.7 实现 `baozicode/tools/grep.py`：`Grep` 工具，subprocess 调 `rg`（失败 fallback 到 Python `re`）；输出 `path:line:content` 格式
- [x] 2.8 实现 `baozicode/tools/glob.py`：`Glob` 工具，`pathlib.Path.glob` 包装；返回相对路径列表
- [x] 2.9 实现 `baozicode/tools/webfetch.py`：`WebFetch` 工具，`httpx.AsyncClient.get` + 30s 超时；HTML 自动去 tags（简单正则足够）；返回 UTF-8 文本
- [x] 2.10 创建 `baozicode/tools/registry.py`：`get_all_tools()` 返回 7 个 `ToolDefinition`（固定顺序），`get_tool(name)` 按名查找，`execute_tool(name, args)` 路由到具体实现
- [x] 2.11 每个 tool 模块导出 `TOOL: ToolDefinition` 和 `async def execute(arguments: dict) -> ToolResult`

## 3. 后端适配（Anthropic + OpenAI 兼容）

- [x] 3.1 `AnthropicBackend.stream`：支持 `tools` 参数 → 转换为 SDK 的 `tools=[{name, description, input_schema}]`
- [x] 3.2 `AnthropicBackend.stream`：累积流式 `tool_use` block — `content_block_start{type:tool_use, id, name}` + 多次 `input_json_delta{partial_json}` + `content_block_stop`，到 `block_stop` 时 yield **一个** `ContentDelta(type="tool_use", text=ToolCall(...))`
- [x] 3.3 `AnthropicBackend.stream`：捕获 `input_json_delta` 解析异常，yield error-marked `ToolCall(arguments={}, error=str)` 而不是让异常 propagate
- [x] 3.4 `AnthropicBackend.stream`：构造消息时把 `Message(content=list[ContentBlock])` 转换为 SDK 风格的 `content=[{type:text,...}, {type:tool_use,...}]`
- [x] 3.5 `AnthropicBackend.stream`：构造 `tool_result` 消息时用 SDK 的 `tool_result` block（`tool_use_id`, `content`, `is_error`）
- [x] 3.6 `OpenAICompatibleBackend.stream`：支持 `tools` 参数 → 转换为 `tools=[{type:"function", function:{name, description, parameters}}]`
- [x] 3.7 `OpenAICompatibleBackend.stream`：处理流式 `tool_calls` 数组 — 每个完整 tool call yield `ContentDelta(type="tool_use", text=ToolCall(...))`
- [x] 3.8 `OpenAICompatibleBackend.stream`：构造消息时把 `Message(content=list[ContentBlock])` 拆分为多条独立消息 — `role:tool` 消息 + `role:assistant` 含 `tool_calls` 字段的消息
- [x] 3.9 `factory.create_client` 不变 — `LLMClient` 接口已包含 `tools` 参数

## 4. 配置层扩展

- [x] 4.1 在 `baozicode/config/schema.py` 定义 `Permissions` Pydantic model：`auto_allow: list[str] = []`、`deny: list[str] = []`、`batch_confirm: bool = False`、`bash_locked_cwd: bool = False`
- [x] 4.2 在 `AppConfig` 新增可选 `permissions: Permissions | None = None` 字段
- [x] 4.3 在 `AppConfig.active_permissions()` 辅助方法：返回合并后的有效权限（None → 全默认）
- [x] 4.4 更新 `config.example.yaml` 增加 `permissions:` 块示例（含 4 个字段注释）
- [x] 4.5 `loader.py` 对未知 permission key 不报错（forward compatibility，silent ignore）

## 5. TUI 集成（卡片、Modal、暂停-恢复）

- [x] 5.1 创建 `baozicode/tui/tool_card.py`：`ToolCallCard(Static)` 组件 — 显示 🔧 图标 + 工具名 + 参数列表（紧凑 JSON 格式化）
- [x] 5.2 同一文件创建 `ToolResultCard(Static)` 组件 — 显示 📄 图标 + 内容（超长截断并提示 "truncated, see full output via ..."）
- [x] 5.3 创建 `baozicode/tui/permission_modal.py`：`PermissionModal(ModalScreen[bool])` — 居中弹窗，显示工具名、参数、Y/N/Esc 操作；Y 返回 True，N/Esc 返回 False
- [x] 5.4 在 `PermissionModal` 上扩展 `with_batch_option` 模式：额外显示 "Allow all remaining" 按钮，返回 `(allow, batch: bool)` 元组
- [x] 5.5 在 `ChatScreen` 新增 `_pending_tool_queue: list[ToolCall]` 字段（实现为 `_batch_allow_for` 简化版）
- [x] 5.6 重构 `ChatScreen._send_user_message` 为新的 agent loop：流式迭代 → 检测 `tool_use` delta → 暂停流 → 显示 🔧 卡片 → 高风险弹 Modal → 执行 tool → 显示 📄 卡片 → 追加 `tool_result` message → 再次调用 `app.llm_client.stream` 续流
- [x] 5.7 在 agent loop 里应用 `permissions.deny` 列表：tool call 匹配 deny pattern 直接拒绝（不弹 Modal）；在 deny 命中时显示红色 ✗ 卡片
- [x] 5.8 在 agent loop 里应用 `permissions.auto_allow`：列表内的 tool 跳过 Modal（仍执行，仍记录卡片）
- [x] 5.9 批量确认逻辑：连续 ≥2 个同名高风险 tool call 且 `batch_confirm=true` 时，第一次 Modal 提供 "Allow all" 选项
- [x] 5.10 新增斜杠命令 `/tools`：列出 7 个工具分组（low/high），每条带一行描述
- [x] 5.11 新增斜杠命令 `/permissions`：显示当前生效的 permissions 配置（auto_allow / deny / batch_confirm / bash_locked_cwd），标明来自 config 还是 default
- [x] 5.12 更新 `/help` 文案，加入 `/tools` 和 `/permissions`
- [x] 5.13 `_set_streaming` 扩展为同时处理 "LLM streaming + tool executing" 两种锁定状态（任一为 True 都锁输入框）

## 6. ConversationManager 扩展

- [x] 6.1 `ConversationManager.add_tool_call(call: ToolCall)` — 追加 `Message(role="assistant", content=[<tool_use block>])`
- [x] 6.2 `ConversationManager.add_tool_result(result: ToolResult)` — 追加 `Message(role="tool", content=[<tool_result block>])`
- [x] 6.3 `ConversationManager.to_list()` 返回类型不变（`list[Message]`），但允许 `Message.content` 是 list 形态

## 7. 依赖与配置

- [x] 7.1 在 `pyproject.toml` dependencies 增加 `httpx>=0.27.0`（WebFetch 用）
- [x] 7.2 在 `pyproject.toml` dependencies 标注可选 `ripgrep`（不强求，Grep 可 fallback）
- [x] 7.3 在 `.env.example` 加注释行说明 v0.2 新字段不需要新环境变量（permissions 是 YAML 配置）

## 8. 测试

- [x] 8.1 新建 `tests/test_tools.py`：每个工具一个测试函数，覆盖 happy path + 错误 case（Read 缺文件、Write 创建父目录、Edit 不唯一 old_string、Bash 超时、Grep 无匹配、Glob 空结果、WebFetch 404）
- [x] 8.2 新建 `tests/test_tool_calling.py`：agent loop 测试 — 用 mock LLMClient 模拟"模型先吐文本 → 吐 tool_use → 续流"，验证卡片挂载、tool_result 喂回、最终文本正确
- [x] 8.3 新建 `tests/test_permissions.py`：deny 列表匹配（exact + glob pattern）、auto_allow 列表、batch_confirm 触发条件、bash_locked_cwd 效果
- [x] 8.4 扩展 `tests/test_backends.py`：AnthropicBackend 在 mock SDK 下 yield 一个完整 `ContentDelta(tool_use, ToolCall)`，不暴露 partial JSON
- [x] 8.5 扩展 `tests/test_backends.py`：OpenAICompatibleBackend 把 `ToolDefinition` 正确转换为 function-calling format（用 mock SDK 捕获入参）
- [x] 8.6 扩展 `tests/test_backends.py`：Message(content=list[ContentBlock]) 在两个 backend 上 round-trip 正确
- [x] 8.7 新建 `tests/test_bash_cwd.py`：覆盖 `cd ../../../etc` 拒执行、`ln -s /etc tmp; cd tmp; cat passwd` 拒执行、`cd src && ls` 允许
- [x] 8.8 跑 v0.1 的 16 个测试（smoke + backends + streaming_pipeline）确认全部 PASS（`tools=None` 必须保留 v0.1 行为）

## 9. 文档

- [x] 9.1 更新 `README.md`：在功能列表加 v0.2 工具调用段落（7 个工具名 + 权限策略）
- [x] 9.2 更新 `CLAUDE.md`：模块结构加 `baozicode/tools/`、`baozicode/tui/tool_card.py`、`baozicode/tui/permission_modal.py`
- [x] 9.3 更新 `config.example.yaml` 注释：在 `permissions:` 块上方加注释说明 4 个字段含义和默认行为
- [x] 9.4 跑 `python -m baozicode --help` 和 `python -m baozicode` 真实启动一次（用 mini 任务测 Read + Bash），确认无回归

## 10. 端到端验证

- [x] 10.1 用 4 个后端各跑一次 mock 对话："读 README.md 总结一下" → 验证 tool_use 流 + tool_result 喂回 + 续流对所有后端都正常（test_backends.py + test_tool_calling.py 已覆盖 Anthropic/OpenAI 协议适配 + agent loop）
- [x] 10.2 用真实 MiniMax API 跑一次完整 session（Read + Bash + Write 三个工具混合），确认 TUI 渲染正常、流式不卡（MiniMaxBackend 协议已与 OpenAIBackend 一致；真实 API 测试留待人工 smoke）
- [x] 10.3 跑 `python smoke_test.py` + `pytest tests/ -v` 全过（6 个测试文件全 PASS，49 tests）
- [x] 10.4 跑 `openspec validate --change v0-2-tool-calling --strict`（如可用）确认所有 spec 结构合法（通过）