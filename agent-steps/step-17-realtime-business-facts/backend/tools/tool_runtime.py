"""Step 17：工具执行层。只读业务工具在这里校验当前用户身份并读取真实业务事实。"""

from __future__ import annotations

from typing import Any

from tools.runtime_context import order_no, order_status


def summarize_order_logistics(order: dict[str, Any], logistics: dict[str, Any] | None) -> str:
    """把订单事实和物流事实整理成可直接回答用户的摘要。"""
    target_order_no = order_no(order)
    payment_status = str(order.get("paymentStatus") or "").strip()
    fulfillment_status = str(order.get("fulfillmentStatus") or "").strip()
    status = order_status(order)
    if payment_status == "UNPAID":
        return f"{target_order_no} 当前还未支付，订单尚未进入发货流程。"
    if fulfillment_status in {"UNSHIPPED", "PENDING_SHIPMENT"} or status in {"PENDING_PAYMENT", "PAID_PENDING_SHIPMENT", "PENDING_SHIPMENT"}:
        return f"{target_order_no} 当前状态是 {status or fulfillment_status}，还没有发货，因此暂时没有物流轨迹。"
    if logistics:
        latest = logistics.get("latestUpdate") or "暂无最新轨迹"
        return f"{target_order_no} 物流状态：{logistics.get('status')}，承运方：{logistics.get('company')}，最新轨迹：{latest}。"
    return f"{target_order_no} 当前状态是 {status or fulfillment_status}，暂未查到独立物流轨迹。"
