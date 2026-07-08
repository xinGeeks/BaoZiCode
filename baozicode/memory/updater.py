"""MemoryUpdater — Agent Loop 自然停下时异步更新两层笔记。

按 openspec/changes/v0-8-memory-and-sessions/specs/auto-memory/spec.md
"MemoryUpdater.update(snapshot)" 段:
- 触发条件: Agent.run() 在 COMPLETED / MAX_ITERATIONS_REACHED 时
- 异步: asyncio.create_task(updater.update(snapshot))
- 静默: LLM 错误 / parse 失败 / store 错误都 log warning 后 return, 不影响 Agent
- 隔离: 跨 session 删除 / 更新默认拒绝
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from baozicode.llm.base import LLMClient, Message
from baozicode.memory.overflow import _parse_fenced_json
from baozicode.memory.prompt import build_extraction_messages
from baozicode.memory.schema import Note, NoteType
from baozicode.memory.store import MemoryStore

if TYPE_CHECKING:
    from baozicode.config.schema import MemoryConfig
    from baozicode.memory.overflow import MemoryOverflowHandler


log = logging.getLogger(__name__)


class MemoryUpdater:
    """异步更新两层笔记(user + project)+ 调度 overflow 评估。

    Args:
        llm: 用于提取笔记的 LLM 客户端
        user_store: 用户级 store(跨项目)
        project_store: 项目级 store(仅本项目)
        config: MemoryConfig(recent_turns_for_update 等)
        current_session_id_fn: 返回当前 session_id 的函数(为 None 时跳跨 session 保护)
        overflow: 可选 overflow handler, 每次 update 后调 check_and_act
    """

    def __init__(
        self,
        llm: LLMClient,
        user_store: MemoryStore,
        project_store: MemoryStore,
        config: "MemoryConfig",
        current_session_id_fn: Callable[[], str] | None = None,
        overflow: "MemoryOverflowHandler | None" = None,
    ) -> None:
        self._llm = llm
        self._user_store = user_store
        self._project_store = project_store
        self._cfg = config
        self._current_session_id_fn = current_session_id_fn
        self._overflow = overflow
        # 监控指标
        self.update_call_count = 0
        self.parse_failures = 0
        self.applied_ops_count = 0
        self.llm_error_count = 0

    async def update(self, messages_snapshot: list[Message]) -> None:
        """从 snapshot 末尾取 N 轮对话, 调 LLM 提取, 应用到两层 store。

        静默: 任何步骤出错 log warning 后 return。
        """
        self.update_call_count += 1
        snapshot_session_id = self._current_session_id_fn() if self._current_session_id_fn else None

        # 1) 取末尾 N 轮(简单切: 后 N*2 条消息当 N 轮 user/assistant 配对)
        n = self._cfg.recent_turns_for_update
        recent = _tail_turns(messages_snapshot, n)

        if not recent:
            log.debug("memory updater: snapshot 无消息, 跳过")
            return

        # 2) 构造 prompt
        user_index = self._user_store.read_index()
        project_index = self._project_store.read_index()
        prompt_messages = build_extraction_messages(recent, user_index, project_index)

        # 3) 调 LLM 流式收集文本
        response_text = ""
        try:
            async for delta in self._llm.stream(
                prompt_messages,
                system=None,  # system 已在 prompt_messages[0]
                tools=[],
                cache_breakpoints=None,
            ):
                if delta.type == "text":
                    response_text += delta.text
        except Exception as exc:  # noqa: BLE001
            self.llm_error_count += 1
            log.warning(
                "memory updater: LLM stream error (%s): %s — 跳过本次更新",
                type(exc).__name__, exc,
            )
            return

        # 4) 解析 fenced JSON
        ops = _parse_fenced_json(response_text)
        if ops is None:
            self.parse_failures += 1
            log.warning("memory updater: 解析 fenced JSON 失败, 跳过本次更新")
            return

        # 5) 应用到两层 store
        applied = self._apply_operations(ops, snapshot_session_id)
        self.applied_ops_count += len(applied)
        if applied:
            log.info("memory updater: 应用 %d 条操作", len(applied))

        # 6) 调 overflow handler 评估(如有)
        if self._overflow is not None:
            try:
                self._overflow.check_and_act(
                    self._project_store,
                    auto_compress_runner=self._overflow._auto_compress,  # noqa: SLF001
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("memory updater: overflow check failed: %s", exc)

    # ---- helpers ----

    def _apply_operations(
        self, ops: dict, snapshot_session_id: str | None
    ) -> list[Note]:
        """派发 operations 到对应 store(user-pref/correction → user, project/reference → project)。

        Returns: 成功应用的 Note 列表
        """
        applied: list[Note] = []
        operations = ops.get("operations") or []
        if not isinstance(operations, list):
            log.warning("memory updater: operations 字段不是 list, 跳过")
            return applied

        for op in operations:
            if not isinstance(op, dict):
                continue
            action = op.get("action")
            if action == "add":
                note = self._build_note_from_add(op, snapshot_session_id)
                if note is None:
                    continue
                store = _route_to_store(note.type, self._user_store, self._project_store)
                if store is None:
                    log.warning("memory updater: unknown type %s, 跳过", note.type)
                    continue
                try:
                    store.add_note(note)
                    applied.append(note)
                except (FileExistsError, ValueError) as exc:
                    log.debug("memory updater: add 失败 %s: %s", note.slug, exc)
                except Exception as exc:  # noqa: BLE001
                    log.warning("memory updater: add 异常 %s: %s", note.slug, exc)

            elif action == "update":
                slug = op.get("slug")
                append = op.get("append", "")
                if not slug or not append:
                    continue
                # 找该 slug 在哪一层
                store = self._find_store_for_slug(slug)
                if store is None:
                    log.debug("memory updater: update 找不到 slug=%s, 跳过", slug)
                    continue
                try:
                    store.update_note(
                        slug, append,
                        current_session_id=snapshot_session_id,
                        require_same_session=False,  # update 允许跨 session 追加
                    )
                    applied.append(Note(
                        type=NoteType.PROJECT,  # 占位
                        slug=slug, title="", content=append,
                        created_at=datetime.now(timezone.utc),
                        source_session=snapshot_session_id or "",
                    ))
                except (FileNotFoundError, PermissionError) as exc:
                    log.debug("memory updater: update 失败 %s: %s", slug, exc)
                except Exception as exc:  # noqa: BLE001
                    log.warning("memory updater: update 异常 %s: %s", slug, exc)

            elif action == "delete":
                slug = op.get("slug")
                if not slug:
                    continue
                store = self._find_store_for_slug(slug)
                if store is None:
                    log.debug("memory updater: delete 找不到 slug=%s, 跳过", slug)
                    continue
                try:
                    store.delete_note(
                        slug,
                        current_session_id=snapshot_session_id,
                        require_same_session=True,  # 跨 session 删需要 source_session 匹配
                    )
                    applied.append(Note(
                        type=NoteType.PROJECT,  # 占位
                        slug=slug, title="", content="(deleted)",
                        created_at=datetime.now(timezone.utc),
                        source_session=snapshot_session_id or "",
                    ))
                except (FileNotFoundError, PermissionError) as exc:
                    log.debug("memory updater: delete 失败 %s: %s", slug, exc)
                except Exception as exc:  # noqa: BLE001
                    log.warning("memory updater: delete 异常 %s: %s", slug, exc)
            # 其它 action 忽略
        return applied

    def _build_note_from_add(
        self, op: dict, snapshot_session_id: str | None
    ) -> Note | None:
        """从 LLM 的 add operation 构造 Note。"""
        try:
            note_type = NoteType(op.get("type", ""))
        except ValueError:
            log.warning("memory updater: add 无效 type=%s, 跳过", op.get("type"))
            return None
        slug = op.get("slug", "")
        title = op.get("title", "")
        content = op.get("content", "")
        if not slug or not title or not content:
            log.warning("memory updater: add 缺字段(slug/title/content), 跳过")
            return None
        tags = op.get("tags", []) or []
        if not isinstance(tags, list):
            tags = []
        return Note(
            type=note_type,
            slug=slug,
            title=title,
            content=content,
            created_at=datetime.now(timezone.utc),
            source_session=snapshot_session_id or "",
            tags=[str(t) for t in tags],
        )

    def _find_store_for_slug(self, slug: str) -> MemoryStore | None:
        """在两层 store 找该 slug 属于哪一层。user 优先。"""
        if (self._user_store.root / f"{slug}.md").exists():
            return self._user_store
        if (self._project_store.root / f"{slug}.md").exists():
            return self._project_store
        return None


def _route_to_store(
    note_type: NoteType,
    user_store: MemoryStore,
    project_store: MemoryStore,
) -> MemoryStore | None:
    """按 type 路由 — user-pref/correction → user, project/reference → project。"""
    if note_type in (NoteType.USER_PREF, NoteType.CORRECTION):
        return user_store
    if note_type in (NoteType.PROJECT, NoteType.REFERENCE):
        return project_store
    return None


def _tail_turns(messages: list[Message], n: int) -> list[Message]:
    """取末尾 N 轮(简化: N*2 条消息, 涵盖 user/assistant/tool)。

    一轮 ≈ 1 user + 1 assistant(+ 可选 tool 配对)。N=5 估 10 条。
    """
    if n <= 0 or not messages:
        return []
    return list(messages[-(n * 2):])


__all__ = ["MemoryUpdater"]
