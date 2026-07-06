"""Agent loop 端到端测试 — 用 mock LLMClient 模拟"文本 → tool_use → 续流"。"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from baozicode.config.schema import AppConfig, BackendConfig, Permissions
from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message
from baozicode.tools.base import ToolCall
from baozicode.tools.registry import execute_tool


class ScriptedClient(LLMClient):
    """按 call_count 返回预定义的流序列,模拟 agent loop 的多次 LLM 调用。

    每个 entry 是 list[ContentDelta],第一次 stream() 返回 entries[0],等等。
    """

    def __init__(self, entries: list[list[ContentDelta]]) -> None:
        self.entries = entries
        self.call_count = 0
        self.last_tools = None
        self.last_messages: list[Message] = []

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
        tools=None,
    ) -> AsyncIterator[ContentDelta]:
        idx = min(self.call_count, len(self.entries) - 1)
        self.call_count += 1
        self.last_messages = list(messages)
        self.last_tools = tools
        for d in self.entries[idx]:
            yield d


async def run_agent_loop_once(
    conv: ConversationManager,
    client: ScriptedClient,
    cfg: AppConfig,
    tools: list,
    user_text: str = "",
    allow_check=None,
) -> None:
    """简化版 agent loop(不挂 UI),跑通一次完整 turn。

    allow_check: callable(call) -> bool;默认全 True。
    """
    if user_text:
        conv.add_user(user_text)
    full_text = ""
    pending: list[ToolCall] = []
    async for delta in client.stream(
        conv.to_list(),
        system=cfg.system_prompt,
        tools=tools,
    ):
        if delta.type == "text":
            full_text += delta.text
        elif delta.type == "tool_use":
            pending.append(delta.text)

    if not pending:
        if full_text:
            conv.add_assistant(full_text)
        return

    from baozicode.llm.base import TextBlock, ToolUseBlock

    blocks: list = []
    if full_text:
        blocks.append(TextBlock(text=full_text))
    for c in pending:
        blocks.append(ToolUseBlock(id=c.id, name=c.name, input=c.arguments))
    conv.add_message(Message(role="assistant", content=blocks))

    for call in pending:
        if allow_check and not allow_check(call):
            from baozicode.tools.base import ToolResult
            r = ToolResult(
                tool_call_id=call.id,
                content="denied",
                is_error=True,
            )
        else:
            r = await execute_tool(call.name, call.arguments, tool_call_id=call.id)
        conv.add_tool_result(r)


def test_text_only_turn() -> None:
    """第一轮:模型只吐文字,没有 tool call → 直接结束。"""
    conv = ConversationManager()
    client = ScriptedClient([[
        ContentDelta(type="text", text="Hi "),
        ContentDelta(type="text", text="there"),
    ]])
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
    )
    asyncio.run(run_agent_loop_once(conv, client, cfg, tools=[], user_text="hi"))
    msgs = conv.to_list()
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant"
    assert msgs[1].content == "Hi there"
    assert client.call_count == 1
    print("[OK] text-only turn")


def test_tool_use_then_resume() -> None:
    """第一轮:模型吐文字 + 1 个 tool call。第二轮:模型拿到 tool_result 再回文字。"""
    call = ToolCall(id="call_1", name="Read", arguments={"file_path": "/etc/hostname"})
    client = ScriptedClient([
        [
            ContentDelta(type="text", text="let me check"),
            ContentDelta(type="tool_use", text=call),
        ],
        [
            ContentDelta(type="text", text="done"),
        ],
    ])
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
    )
    conv = ConversationManager()
    asyncio.run(run_agent_loop_once(conv, client, cfg, tools=[], user_text="check"))
    assert client.call_count == 1  # 只跑了一次完整 turn

    # 第二次跑(因为脚本只跑一轮 agent loop,我们要模拟 LLM 拿到 result 后再调用)
    asyncio.run(run_agent_loop_once(conv, client, cfg, tools=[]))
    msgs = conv.to_list()
    # 期望:user → assistant(text+tool_use) → tool(tool_result) → assistant(text)
    assert len(msgs) == 4, f"got {len(msgs)}: {[m.role for m in msgs]}"
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant"
    # assistant 消息内容是 list[ContentBlock]
    assert isinstance(msgs[1].content, list)
    assert msgs[2].role == "tool"
    assert msgs[3].role == "assistant"
    assert msgs[3].content == "done"
    # 第二次 LLM 调用时,messages 应包含 tool result
    assert any(m.role == "tool" for m in client.last_messages)
    print("[OK] tool_use then resume")


def test_deny_blocks_tool_execution() -> None:
    """permissions.deny 命中的 tool → 执行被拒,返回 is_error result。"""
    call = ToolCall(id="c1", name="Bash", arguments={"command": "ls"})
    client = ScriptedClient([
        [ContentDelta(type="tool_use", text=call)],
        [ContentDelta(type="text", text="ok")],
    ])
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
        permissions=Permissions(deny=["Bash"]),
    )

    def deny_all(c):
        return False  # 模拟 deny 命中

    conv = ConversationManager()
    asyncio.run(run_agent_loop_once(
        conv, client, cfg, tools=[], user_text="ls", allow_check=deny_all,
    ))
    msgs = conv.to_list()
    # 找到 tool result message
    tool_result_msg = next(m for m in msgs if m.role == "tool")
    assert tool_result_msg.content[0].is_error is True
    assert "denied" in tool_result_msg.content[0].content
    print("[OK] deny blocks tool execution")


def test_auto_allow_skips_modal() -> None:
    """permissions.auto_allow 中的 tool 直接执行(不弹 modal,这里用 allow_check 模拟)。"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
        tf.write("hello")
        tmp_path = tf.name
    call = ToolCall(id="c1", name="Read", arguments={"file_path": tmp_path})
    client = ScriptedClient([
        [ContentDelta(type="tool_use", text=call)],
        [ContentDelta(type="text", text="got it")],
    ])
    cfg = AppConfig(
        backend="anthropic",
        anthropic=BackendConfig(api_key="x", model="m"),
        openai=BackendConfig(api_key="x", model="m"),
        minimax=BackendConfig(api_key="x", model="m"),
        deepseek=BackendConfig(api_key="x", model="m"),
        permissions=Permissions(auto_allow=["Read"]),
    )

    def check(c):
        # auto_allow 时不弹 modal(allow_check 不会跑到 deny 路径)
        from baozicode.tools.registry import get_tool
        return c.name in cfg.active_permissions().auto_allow or get_tool(c.name).risk == "low"

    conv = ConversationManager()
    asyncio.run(run_agent_loop_once(
        conv, client, cfg, tools=[], user_text="read", allow_check=check,
    ))
    msgs = conv.to_list()
    tool_result_msg = next(m for m in msgs if m.role == "tool")
    # Read 在 Windows 上不一定能读 /etc/hostname,但成功/失败都通过 allow_check
    # 关键:不被拒(is_error=False 因为 allow_check=True)
    assert tool_result_msg.content[0].is_error is False
    print("[OK] auto_allow proceeds")


if __name__ == "__main__":
    test_text_only_turn()
    test_tool_use_then_resume()
    test_deny_blocks_tool_execution()
    test_auto_allow_skips_modal()
    print("\nAll agent loop tests passed.")