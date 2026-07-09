"""HookRegistry:从 AppConfig 加载 + freeze 集中校验 + 创建 dispatcher。

启动期:
1. HookRegistry.load(app_config) 把 `app_config.hooks` 转 HookDefYaml,收集所有错误
2. freeze() 跑非 Pydantic 校验规则(id 唯一 / event 在 ALL_EVENTS / async+tool.pre /
   if.all+if.any 互斥 / slot+event 互斥 等)
3. create_dispatcher(agent) 串起 HookDispatcher 实例

错误不 bail 在第一个;启动错误消息格式:
    ERROR: hooks validation failed (3 errors):
      - hooks[audit-bash-pre]: empty actions list
      - hooks[bad-async]: async not allowed for tool.pre events
      - hooks[no-id]: missing required field 'id'
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from baozicode.hooks._errors import HookValidationError  # re-export
from baozicode.hooks.schema import (
    ALL_EVENTS,
    STABLE_SYSTEM_FORBIDDEN_EVENTS,
    SYNC_ONLY_EVENTS,
    HookDefYaml,
    parse_hook_def,
)

if TYPE_CHECKING:
    from baozicode.hooks.dispatcher import HookDispatcher


log = logging.getLogger(__name__)


class HookRegistry:
    """加载 + 冻结 + 查询 hook 规则的中心仓库。"""

    def __init__(self, hooks: list[HookDefYaml]) -> None:
        self._hooks: list[HookDefYaml] = hooks
        # 按 event 索引,保持声明顺序
        self._by_event: dict[str, list[HookDefYaml]] = {}
        for h in hooks:
            self._by_event.setdefault(h.event, []).append(h)

    @classmethod
    def load(cls, raw_hooks: list[dict[str, Any]] | None) -> "HookRegistry":
        """解析 YAML dict → HookDefYaml 列表。None 视为空。

        收集所有 Pydantic ValidationError 一次性抛 HookValidationError。
        """
        if not raw_hooks:
            return cls([])
        parsed: list[HookDefYaml] = []
        all_errors: list[dict[str, Any]] = []
        for i, raw in enumerate(raw_hooks):
            try:
                parsed.append(parse_hook_def(raw))
            except HookValidationError as exc:
                # 把 index 当 hook_id 写到错误中,让用户能定位是哪一行
                for err in exc.errors:
                    err_copy = dict(err)
                    err_copy["hook_id"] = err_copy.get("hook_id", f"<hooks[{i}]>")
                    all_errors.append(err_copy)
            except ValidationError as exc:
                all_errors.append({
                    "hook_id": f"<hooks[{i}]>",
                    "field": "<root>",
                    "reason": str(exc),
                })
        if all_errors:
            raise HookValidationError(all_errors)
        return cls(parsed)

    def freeze(self) -> None:
        """运行非 Pydantic 校验规则。失败抛 HookValidationError 一次性聚合。"""
        errors: list[dict[str, Any]] = []
        seen_ids: dict[str, int] = {}

        for i, hook in enumerate(self._hooks):
            hid = hook.id
            # id 唯一
            if hid in seen_ids:
                errors.append({
                    "hook_id": hid,
                    "field": "id",
                    "reason": f"duplicate hook id (also defined at hooks[{seen_ids[hid]}])",
                })
            else:
                seen_ids[hid] = i

            # event 合法性(Pydantic 已经验过,这里再保险)
            if hook.event not in ALL_EVENTS:
                errors.append({
                    "hook_id": hid,
                    "field": "event",
                    "reason": f"unknown event '{hook.event}'",
                })

            # actions 非空(Pydantic 已经验过 min_length=1,保留符号位)
            if not hook.actions:
                errors.append({
                    "hook_id": hid,
                    "field": "actions",
                    "reason": "empty actions list",
                })

            # tool.pre 不允许 async
            if hook.event in SYNC_ONLY_EVENTS and hook.async_:
                errors.append({
                    "hook_id": hid,
                    "field": "async",
                    "reason": f"async not allowed for {hook.event} events",
                })

            # if.all / if.any 互斥
            if hook.if_ is not None:
                has_all = bool(hook.if_.all)
                has_any = bool(hook.if_.any)
                if has_all and has_any:
                    errors.append({
                        "hook_id": hid,
                        "field": "if",
                        "reason": "if.all and if.any are mutually exclusive",
                    })

            # prompt 配 stable_system 在 tool.pre/post 禁止
            if hook.event in STABLE_SYSTEM_FORBIDDEN_EVENTS:
                for j, action in enumerate(hook.actions):
                    if action.action == "prompt" and action.slot == "stable_system":
                        errors.append({
                            "hook_id": hid,
                            "field": f"actions[{j}].slot",
                            "reason": f"slot=stable_system not allowed for {hook.event} events",
                        })

        if errors:
            raise HookValidationError(errors)

    def list_hooks(self, event: str) -> list[HookDefYaml]:
        """返回匹配 event 的 hook 列表,按 YAML 声明顺序。"""
        return list(self._by_event.get(event, ()))

    def all_hooks(self) -> list[HookDefYaml]:
        return list(self._hooks)

    def create_dispatcher(self, agent: Any) -> "HookDispatcher":
        """构造 HookDispatcher 实例,持有 agent 反向引用(只用于 enqueue_reminder)。"""
        from baozicode.hooks.dispatcher import HookDispatcher
        return HookDispatcher(registry=self, agent=agent)


__all__ = ["HookRegistry", "HookValidationError"]
