"""Step 26：运行时上下文整理。只暴露当前用户可用的订单和页面摘要。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *


def runtime_context(request: ChatRequest) -> dict[str, Any]:
    """读取请求里的运行时上下文。"""
    return request.runtime_context if isinstance(request.runtime_context, dict) else {}

def as_order_list(value: Any) -> list[dict[str, Any]]:
    """把订单上下文规整成订单列表。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

def order_no(order: dict[str, Any]) -> str:
    """提取统一订单号。"""
    return str(order.get("orderNo") or order.get("order_id") or "").strip()

def order_user_id(order: dict[str, Any]) -> str:
    """提取订单所属用户。"""
    return str(order.get("userId") or order.get("user_id") or "").strip()

def order_status(order: dict[str, Any]) -> str:
    """提取统一订单状态。"""
    return str(order.get("orderStatus") or order.get("order_status") or order.get("status") or "").strip()

def payment_status(order: dict[str, Any]) -> str:
    """提取支付状态。"""
    return str(order.get("paymentStatus") or order.get("payment_status") or "").strip()

def fulfillment_status(order: dict[str, Any]) -> str:
    """提取履约状态。"""
    return str(order.get("fulfillmentStatus") or order.get("fulfillment_status") or order_status(order)).strip()

def signed_date(order: dict[str, Any]) -> str | None:
    """提取签收日期。"""
    value = order.get("deliveredAt") or order.get("signed_date")
    return str(value) if value else None

def current_user_orders(request: ChatRequest) -> list[dict[str, Any]]:
    """读取当前用户订单上下文。"""
    context = runtime_context(request)
    orders = as_order_list(context.get("currentUserOrders"))
    related_order_no = str(context.get("relatedOrderNo") or "").strip()
    if related_order_no and all(order_no(order) != related_order_no for order in orders):
        orders.append({"orderNo": related_order_no, "status": "已从当前页面带入"})
    return orders

def public_runtime_context(request: ChatRequest) -> dict[str, Any]:
    """生成可展示的上下文摘要。"""
    context = runtime_context(request)
    return {
        "currentPage": context.get("currentPage"),
        "relatedProductId": context.get("relatedProductId"),
        "relatedOrderNo": context.get("relatedOrderNo"),
        "relatedAfterSaleNo": context.get("relatedAfterSaleNo"),
        "currentUserOrderCount": len(as_order_list(context.get("currentUserOrders"))),
    }

def find_context_order(request: ChatRequest, target_order_no: str) -> dict[str, Any] | None:
    """在当前用户上下文里查找订单。"""
    target = target_order_no.lower()
    for order in current_user_orders(request):
        if order_no(order).lower() == target:
            return order
    return None
