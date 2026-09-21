"""Step 21：Observation 压缩层。这里把内部 ToolResult 变成可进入回答上下文的摘要。"""

from __future__ import annotations

from typing import Any

from api.schemas import *
from tools.runtime_context import *


def latest_logistics_event(logistics: dict[str, Any] | None) -> str:
    """执行 latest_logistics_event 对应的项目逻辑。"""
    if not isinstance(logistics, dict):
        return "暂无物流轨迹"
    events = logistics.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            return str(first.get("description") or first.get("status") or first.get("occurredAt") or "暂无物流轨迹")
        return str(first)
    return str(logistics.get("latestUpdate") or logistics.get("status") or "暂无物流轨迹")

def build_observation(tool_result: ToolResult) -> Observation:
    """把内部工具结果压缩成可回答的 Observation。"""
    payload = tool_result.raw_payload
    if tool_result.status == "error":
        next_action: NextAction = "fallback_answer" if tool_result.error_category in {"timeout", "model_unavailable"} else "answer_user"
        return Observation(
            tool_name=tool_result.tool_name,
            status="error",
            summary=payload.get("error", "工具返回错误。"),
            facts={"error_category": tool_result.error_category},
            omitted_fields=[],
            next_action=next_action,
            error_category=tool_result.error_category,
        )
    if tool_result.tool_name == "get_order_logistics":
        order = payload["order"]
        logistics = payload.get("logistics")
        target_order_no = order_no(order)
        logistics_status = str((logistics or {}).get("status") or "暂无物流状态")
        latest_event = latest_logistics_event(logistics)
        estimated_delivery = (logistics or {}).get("estimatedDelivery")
        return Observation(
            tool_name=tool_result.tool_name,
            status="success",
            summary=f"{target_order_no} 物流状态：{logistics_status}，最新节点：{latest_event}。",
            facts={"order_id": target_order_no, "logistics_status": logistics_status, "latest_event": latest_event, "estimated_delivery": estimated_delivery},
            omitted_fields=["events", "trackingNo", "order.items"],
            next_action="answer_user",
        )
    if tool_result.tool_name == "get_refund_status":
        order = payload["order"]
        target_order_no = order_no(order)
        return Observation(
            tool_name=tool_result.tool_name,
            status="success",
            summary=f"{target_order_no} 退款进度：{payload['refund_status']}。",
            facts={"order_id": target_order_no, "refund_status": payload["refund_status"]},
            omitted_fields=["order.items", "order.totalAmount", "order.remark"],
            next_action="answer_user",
        )
    product = payload["product"]
    sku = str(product.get("code") or product.get("sku") or "")
    inventory = product.get("stock") if product.get("stock") is not None else product.get("inventory")
    price = product.get("price") if product.get("price") is not None else product.get("current_price")
    return Observation(
        tool_name=tool_result.tool_name,
        status="success",
        summary=f"{product.get('name', sku)} 当前库存 {inventory} 件，当前价 {price} 元。",
        facts={"sku": sku, "inventory": inventory, "current_price": price},
        omitted_fields=["description", "highlights", "promotion"],
        next_action="answer_user",
    )
