## 1. 抽象基类重构

- [x] 1.1 在 `llm/openai.py` 中抽出 `OpenAICompatibleBackend(LLMClient)` 抽象基类，承载所有 OpenAI 兼容后端的共同实现（`AsyncOpenAI` 客户端构造 + `stream()` 流式逻辑）
- [x] 1.2 把 `OpenAIBackend` 改为 `OpenAICompatibleBackend` 的子类，仅声明 `DEFAULT_BASE_URL` 和 `DEFAULT_MODEL` 两个类属性
- [x] 1.3 `OpenAICompatibleBackend` 构造函数：`model` 和 `base_url` 都接受 `None`，缺省时用类属性 `DEFAULT_*`

## 2. 新增两个后端

- [x] 2.1 新建 `llm/MiniMax.py`，定义 `MiniMaxBackend(OpenAICompatibleBackend)`，默认 base_url `https://api.minimaxi.com/v1`、默认 model `MiniMax-M3`
- [x] 2.2 新建 `llm/deepseek.py`，定义 `DeepSeekBackend(OpenAICompatibleBackend)`，默认 base_url `https://api.deepseek.com/v1`、默认 model `deepseek-chat`
- [x] 2.3 本地验证：构造 3 个子类实例，断言都继承自 `OpenAICompatibleBackend`，且 `DEFAULT_*` 不同

## 3. 配置层扩展

- [x] 3.1 在 `config/schema.py` 把 `BackendName` 改为 `Literal["anthropic", "openai", "minimax", "deepseek"]`
- [x] 3.2 `AppConfig` 增加 `minimax: BackendConfig` 和 `deepseek: BackendConfig` 两个必填字段
- [x] 3.3 更新 `config.example.yaml` 增加两套后端配置块
- [x] 3.4 更新 `.env.example` 增加 `MINIMAX_API_KEY` 和 `DEEPSEEK_API_KEY` 注释行
- [x] 3.5 写一个验证脚本，断言 4 后端都能被 factory 正确创建

## 4. 工厂扩展

- [x] 4.1 在 `llm/factory.py` 把 backend 名称映射到对应类，4 个 entry：`anthropic` / `openai` / `minimax` / `deepseek`
- [x] 4.2 重构为 dict 映射 + 查表，减少 if/elif

## 5. App 与 /model 改造

- [x] 5.1 把 `App.switch_backend()` 改为接受 `target: BackendName` 参数，调用方显式指定目标
- [x] 5.2 在 `App` 上增加 `available_backends()` 辅助方法返回 4 个后端的 `[(name, model_label), ...]` 列表
- [x] 5.3 重构 `ModelSelectScreen`：从"两个按钮"改为"4 个按钮列表 + 取消"，高亮当前后端
- [x] 5.4 在 `ChatScreen._switch_model` 中用新接口：调用 `app.available_backends()` 拿到选项列表，构造 `ModelSelectScreen`
- [x] 5.5 测试 `/model` 选不同后端，确认 `app.config.backend` 和 `app.llm_client` 类型都正确更新

## 6. 文档更新

- [x] 6.1 更新 `README.md`：在项目介绍中加上 MiniMax 和 DeepSeek 表格行；标注 MiniMax 的 base_url 是占位
- [x] 6.2 更新 `CLAUDE.md`：在模块结构里加上 `llm/MiniMax.py` 和 `llm/deepseek.py`；dependencies section 标注 4 个后端
- [x] 6.3 更新 config.example.yaml 的注释，说明 `minimax:` 和 `deepseek:` 是必填块（即使当前用 anthropic 也要填）

## 7. 回归测试

- [x] 7.1 重跑 `smoke_test.py` 和 `tests/test_streaming_pipeline.py`，确认 v0.1 的 8 个测试不回归
  - 旧 smoke_test.py 因 schema 升级（4 块全必填）需要更新 → 已同步更新
- [x] 7.2 跑 `baozicode --help` 确认 CLI 仍正常
- [x] 7.3 启动 TUI 一次（用 4 后端 config），确认 4 个后端的 config 都能被 schema 接受、TUI 正常启动
- [x] 7.4 写一个新的 `test_backends.py`，断言 4 个后端都满足 `LLMClient` 接口、都被 factory 正确创建
