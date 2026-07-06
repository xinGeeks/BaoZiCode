"""用 mock backend 验证流式管线和 TUI 状态机的集成。"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).parent))

from baozicode.conversation.manager import ConversationManager
from baozicode.llm.base import ContentDelta, LLMClient, Message


class MockClient(LLMClient):
    """模拟流式输出：分 3 个 chunk yield 完整回答。"""

    def __init__(self, chunks: list[str], fail: bool = False) -> None:
        self.chunks = chunks
        self.fail = fail
        self.call_count = 0
        self.last_messages: list[Message] = []
        self.last_system: str | None = None

    async def stream(
        self,
        messages: list[Message],
        system: str | None = None,
    ) -> AsyncIterator[ContentDelta]:
        self.call_count += 1
        self.last_messages = list(messages)
        self.last_system = system
        for chunk in self.chunks:
            yield ContentDelta(type="text", text=chunk)
            await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("simulated network failure")


async def simulate_send(conv: ConversationManager, client: LLMClient, user_text: str) -> str:
    """模拟 chat_screen._send_user_message 的核心流式逻辑。"""
    conv.add_user(user_text)
    full_response = ""
    try:
        async for delta in client.stream(conv.to_list(), system="You are BaoZiCode"):
            if delta.type == "text" and delta.text:
                full_response += delta.text
    except Exception as e:
        full_response += f"\n[ERROR: {e}]"
    conv.add_assistant(full_response)
    return full_response


async def main() -> None:
    # Test 1: multi-turn
    conv = ConversationManager()
    client = MockClient(["Hello", " there", "!"])
    r1 = await simulate_send(conv, client, "hi")
    assert r1 == "Hello there!"
    assert client.call_count == 1
    assert len(client.last_messages) == 1
    assert client.last_messages[0].content == "hi"
    assert client.last_system == "You are BaoZiCode"

    r2 = await simulate_send(conv, client, "how are you?")
    assert len(client.last_messages) == 3  # 包含完整历史
    assert client.last_messages[-1].content == "how are you?"
    print("[OK] multi-turn: history sent correctly")

    # Test 2: error handling
    conv2 = ConversationManager()
    err_client = MockClient(["partial "], fail=True)
    r = await simulate_send(conv2, err_client, "test")
    assert r.startswith("partial ")
    assert "ERROR" in r
    # 错误时仍然要把 assistant 消息加入（包含部分 + 错误）
    assert conv2.to_list()[-1].role == "assistant"
    print("[OK] error mid-stream: partial response + error preserved")

    # Test 3: clear
    conv.clear()
    assert len(conv) == 0
    r3 = await simulate_send(conv, client, "fresh start")
    assert len(client.last_messages) == 1  # 只有当前消息
    assert client.last_messages[0].content == "fresh start"
    print("[OK] after /clear: history reset")

    print("\nAll pipeline tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
