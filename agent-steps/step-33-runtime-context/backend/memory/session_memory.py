"""Step 33 沿用的短期 Session Memory。

这里只保留 Step 32 已经讲清楚的最近订单线索；Runtime Context 仍然负责身份、
会员和权限判断，记忆不能证明订单归属。
"""

from __future__ import annotations

from threading import RLock
from typing import Any

from api.schemas import Intent, MemoryDecision, SessionMemorySnapshot
from tools.runtime_context import order_no

SESSION_MEMORIES: dict[str, SessionMemorySnapshot] = {}
SESSION_MEMORY_OWNERS: dict[str, str] = {}
SESSION_MEMORY_LOCK = RLock()


class SessionMemoryStore:
    """保存当前 session 里已通过权限校验的最近订单。"""

    def get(self, session_id: str, runtime_user_id: str) -> SessionMemorySnapshot:
        """读取当前用户的短期记忆；会话换人时丢弃旧用户状态。"""
        with SESSION_MEMORY_LOCK:
            memory = SESSION_MEMORIES.get(session_id)
            if memory is None or SESSION_MEMORY_OWNERS.get(session_id) != runtime_user_id:
                # Runtime Context 决定属主；两张字典必须原子更新，不能只凭 session_id 复用记忆。
                memory = SessionMemorySnapshot()
                SESSION_MEMORIES[session_id] = memory
                SESSION_MEMORY_OWNERS[session_id] = runtime_user_id
            return memory

    def update(
        self,
        *,
        memory: SessionMemorySnapshot,
        intent: Intent,
        owned_order: dict[str, Any] | None,
    ) -> list[MemoryDecision]:
        """只在订单通过 Runtime Context 权限校验后写入最近订单。"""
        decisions: list[MemoryDecision] = []
        if owned_order is not None:
            memory.last_order_id = order_no(owned_order)
            decisions.append(
                MemoryDecision(
                    key="last_order_id",
                    value=memory.last_order_id,
                    accepted=True,
                    reason="订单已通过 Runtime Context 的当前用户归属校验，可以延续 Step 32 的最近订单记忆。",
                    ttl="session",
                )
            )
        memory.recent_intent = intent
        decisions.append(
            MemoryDecision(
                key="recent_intent",
                value=intent,
                accepted=True,
                reason="最近意图只用于本 session 内追问消歧，不能替代 Runtime Context 权限判断。",
                ttl="session",
            )
        )
        return decisions
