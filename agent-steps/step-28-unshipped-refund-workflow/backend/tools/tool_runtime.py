"""Step 28：工具执行层。这里真正读取业务事实并构造受控工具调用记录。"""

from __future__ import annotations

from typing import Any

from api.schemas import *
from tools.contracts import *
from tools.runtime_context import *
from integrations.ecommerce_client import *


def logistics_status_from_order(order: dict[str, Any], logistics: dict[str, Any] | None = None) -> str:
    """从订单状态推导物流状态。"""
    if isinstance(logistics, dict) and logistics.get("status"):
        return str(logistics.get("status"))
    fulfillment = fulfillment_status(order).upper()
    if fulfillment in {"PENDING_SHIPMENT", "NOT_SHIPPED", "UNSHIPPED"}:
        return "NOT_SHIPPED"
    if fulfillment in {"SHIPPED", "IN_TRANSIT"}:
        return "IN_TRANSIT"
    if fulfillment in {"DELIVERED", "SIGNED"}:
        return "SIGNED"
    return fulfillment or "UNKNOWN"

def make_tool_call(tool_name: str, arguments: dict[str, Any], reason: str, summary: str, facts: dict[str, Any]) -> ToolCallRecord:
    """构造可观察的工具调用记录。"""
    return ToolCallRecord(
        action=ToolAction(tool_name=tool_name, arguments=arguments, reason=reason),
        observation=Observation(
            tool_name=tool_name,
            status="success",
            summary=summary,
            facts=facts,
            omitted_fields=["internal_id", "raw_payload"],
        ),
    )

def load_order(order_id: str, runtime_user_id: str, context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, ToolCallRecord]:
    """读取并校验当前用户订单。"""
    order = find_context_order(context or {}, order_id)
    if order is None:
        order, allowed = order_fact_from_ecommerce(order_id, runtime_user_id)
    else:
        allowed = True
    if order is None:
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "高风险售后必须先查订单事实。",
            "没有查到该订单。",
            {"order_id": order_id, "found": False},
        )
    if not allowed:
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "高风险售后必须绑定当前登录用户。",
            f"{order_id} 不属于当前登录用户，不能继续处理。",
            {"order_id": order_id, "found": True, "owner_matched": False},
        )
    return order, make_tool_call(
        "get_order_detail",
        {"order_id": order_id},
        "退款资格先看订单状态和支付状态。",
        f"{order_id} 履约状态 {fulfillment_status(order)}，支付状态 {payment_status(order)}。",
        {
            "order_id": order_id,
            "order_status": fulfillment_status(order),
            "payment_status": payment_status(order),
            "fulfillment_status": fulfillment_status(order),
            "owner_matched": True,
        },
    )

def load_logistics(order: dict[str, Any], runtime_user_id: str) -> ToolCallRecord:
    """读取订单物流事实。"""
    logistics = logistics_fact_from_ecommerce(order_no(order), runtime_user_id)
    status = logistics_status_from_order(order, logistics)
    return make_tool_call(
        "get_order_logistics",
        {"order_id": order_no(order)},
        "退款资格还要看物流是否已经出库、发货或签收。",
        f"{order_no(order)} 物流状态 {status}。",
        {"order_id": order_no(order), "logistics_status": status, "signed_date": signed_date(order)},
    )
