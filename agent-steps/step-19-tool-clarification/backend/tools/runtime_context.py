"""Step 19：运行时上下文整理。这里把页面带入的当前用户订单转成工具可用的安全事实。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *


def _runtime_context(request: ChatRequest) -> dict[str, Any]:
    """取出前端随请求带入的运行时上下文。"""
    return request.runtime_context if isinstance(request.runtime_context, dict) else {}

def _as_order_list(value: Any) -> list[dict[str, Any]]:
    """把运行时订单上下文规整为订单字典列表。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

def _item_summary(order: dict[str, Any]) -> list[str]:
    """整理订单商品摘要，供澄清候选展示。"""
    items = order.get("itemSummary") or order.get("items") or []
    if isinstance(items, str):
        return [part.strip() for part in re.split(r"[，,、;；]", items) if part.strip()]
    if isinstance(items, list):
        names: list[str] = []
        for item in items:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("productName") or item.get("name")
                quantity = item.get("quantity")
                if name:
                    names.append(f"{name} x{quantity}" if quantity else str(name))
        return names
    return []

def _order_no(order: dict[str, Any]) -> str:
    """从不同字段命名中提取统一订单号。"""
    return str(order.get("orderNo") or order.get("order_id") or "").strip()

def _order_status(order: dict[str, Any]) -> str:
    """从不同字段命名中提取统一订单状态。"""
    return str(order.get("status") or order.get("orderStatus") or "").strip()

def _order_created_month(order: dict[str, Any]) -> int | None:
    """从订单创建时间中提取月份。"""
    created_at = str(order.get("createdAt") or order.get("created_at") or "")
    match = re.search(r"^\d{4}-(\d{1,2})-", created_at)
    return int(match.group(1)) if match else None

def current_user_orders(request: ChatRequest) -> list[dict[str, Any]]:
    """读取已由业务后端校验过的当前用户订单候选。"""
    context = _runtime_context(request)
    orders = _as_order_list(context.get("currentUserOrders"))
    related_order_no = str(context.get("relatedOrderNo") or "").strip()
    if related_order_no and all(_order_no(order) != related_order_no for order in orders):
        orders.append({"orderNo": related_order_no, "status": "已从当前页面带入", "itemSummary": []})
    return orders

def current_user_orders_truncated(request: ChatRequest) -> bool:
    """标记订单候选是否只是一个不完整窗口，避免把未加载订单误判成不存在。"""
    return _runtime_context(request).get("currentUserOrdersTruncated") is True

def public_runtime_context(request: ChatRequest) -> dict[str, Any]:
    """生成可展示的上下文摘要，避免暴露完整订单明细。"""
    context = _runtime_context(request)
    return {
        "currentPage": context.get("currentPage"),
        "relatedProductId": context.get("relatedProductId"),
        "relatedOrderNo": context.get("relatedOrderNo"),
        "relatedAfterSaleNo": context.get("relatedAfterSaleNo"),
        "currentUserOrderCount": len(_as_order_list(context.get("currentUserOrders"))),
        "currentUserOrdersTruncated": current_user_orders_truncated(request),
    }

def user_order_candidates(request: ChatRequest) -> list[ClarificationCandidate]:
    """把当前用户订单转换成澄清候选项。"""
    return order_candidates_from_orders(current_user_orders(request))

def order_candidates_from_orders(orders: list[dict[str, Any]]) -> list[ClarificationCandidate]:
    """把订单列表转换成结构化澄清候选项。"""
    return [
        ClarificationCandidate(
            value=_order_no(order),
            label=f"{_order_no(order)}｜{_order_status(order) or '状态待查'}",
            hint="、".join(_item_summary(order)) or "当前用户真实订单",
        )
        for order in orders
        if _order_no(order)
    ]

def find_context_order(request: ChatRequest, order_id: str) -> dict[str, Any] | None:
    """只在当前用户上下文里查找目标订单。"""
    order_id_lower = order_id.lower()
    for order in current_user_orders(request):
        if _order_no(order).lower() == order_id_lower:
            return order
    return None
