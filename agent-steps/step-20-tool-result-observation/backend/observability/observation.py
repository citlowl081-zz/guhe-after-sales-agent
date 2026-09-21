"""Step 20：Observation 压缩层。这里把内部 ToolResult 变成可进入回答上下文的摘要。"""

from __future__ import annotations

from typing import Any

from api.schemas import *
from tools.runtime_context import *


def build_observation(tool_result: ToolResult) -> Observation:
    """把内部工具结果压缩成可回答的 Observation。"""
    payload = tool_result.raw_payload
    if tool_result.status == "error":
        error = payload.get("error", "unknown_error")
        summary_by_error = {
            "order_not_found": "没有查到这个订单。",
            "forbidden": "订单不属于当前登录用户。",
            "product_not_found": "没有查到这个商品。",
        }
        return Observation(
            tool_name=tool_result.tool_name,
            status="error",
            summary=summary_by_error.get(error, "工具返回错误。"),
            facts={"error": error},
            omitted_fields=[],
            next_action="fallback_answer",
        )

    if tool_result.tool_name == "get_order_logistics":
        order = payload["order"]
        logistics = payload.get("logistics")
        if not logistics:
            return Observation(
                tool_name=tool_result.tool_name,
                status="success",
                summary=f"{order_no(order)} 当前订单状态是{order_status(order) or '待查'}，暂未查到独立物流轨迹。",
                facts={"order_id": order_no(order), "order_status": order_status(order)},
                omitted_fields=["order.items", "order.totalAmount", "order.remark"],
                next_action="answer_user",
            )
        events = logistics.get("events") if isinstance(logistics.get("events"), list) else []
        latest_event = logistics.get("latestUpdate") or (events[0].get("content") if events and isinstance(events[0], dict) else "")
        return Observation(
            tool_name=tool_result.tool_name,
            status="success",
            summary=f"{order_no(order)} 物流状态：{logistics.get('status')}，承运方：{logistics.get('company')}，最新轨迹：{latest_event or '暂无最新轨迹'}。",
            facts={
                "order_id": order_no(order),
                "logistics_status": logistics.get("status"),
                "carrier": logistics.get("company"),
                "latest_event": latest_event,
                "estimated_delivery": logistics.get("estimatedDelivery"),
            },
            omitted_fields=["events", "trackingNo", "exceptionReason"],
            next_action="answer_user",
        )

    if tool_result.tool_name == "get_refund_status":
        order = payload["order"]
        return Observation(
            tool_name=tool_result.tool_name,
            status="success",
            summary=f"{order_no(order)} 当前订单状态：{order_status(order) or '待查'}。Step 20 只做只读状态观察，不创建退款申请。",
            facts={"order_id": order_no(order), "order_status": order_status(order)},
            omitted_fields=["order.items", "order.totalAmount", "order.remark"],
            next_action="answer_user",
        )

    product = payload["product"]
    return Observation(
        tool_name=tool_result.tool_name,
        status="success",
        summary=f"{product.get('name')} 当前库存 {product.get('stock')} 件，当前价 {product.get('price')} 元。",
        facts={"sku": product.get("code"), "name": product.get("name"), "current_price": product.get("price"), "inventory": product.get("stock")},
        omitted_fields=["description", "highlights", "promotion", "afterSaleLimit", "scenarioTags"],
        next_action="answer_user",
    )
