"""Step 25：Observation 压缩层。原始工具结果在这里变成安全摘要。"""

from __future__ import annotations

from typing import Any

from api.schemas import *
from tools.runtime_context import *


def latest_logistics_event(logistics: dict[str, Any] | None) -> str:
    """提取物流最新节点。"""
    if not isinstance(logistics, dict):
        return "暂无物流轨迹"
    events = logistics.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            return str(first.get("description") or first.get("status") or first.get("occurredAt") or "暂无物流轨迹")
        return str(first)
    return str(logistics.get("latestUpdate") or logistics.get("status") or "暂无物流轨迹")
