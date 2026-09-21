"""运行时上下文与订单字段规范化。重点是把可信页面上下文和用户自述分开。"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from api.schemas import *

def runtime_context(request: ChatRequest) -> dict[str, Any]:
    """提取本轮可信运行时上下文，和用户自然语言分开处理。"""
    return request.runtime_context if isinstance(request.runtime_context, dict) else {}

def as_order_list(value: Any) -> list[dict[str, Any]]:
    """把页面上下文里的订单字段规范成列表。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

def order_no(order: dict[str, Any]) -> str:
    """从不同订单字段形态里取出稳定订单号。"""
    return str(order.get("orderNo") or order.get("order_id") or "").strip()

def order_user_id(order: dict[str, Any]) -> str:
    """从订单事实里取出归属用户，用于权限校验。"""
    return str(order.get("userId") or order.get("user_id") or "").strip()

def payment_status(order: dict[str, Any]) -> str:
    """读取支付状态，供退款资格判断使用。"""
    return str(order.get("paymentStatus") or order.get("payment_status") or "").strip()

def fulfillment_status(order: dict[str, Any]) -> str:
    """读取履约状态，区分未发货、已发货和已签收路径。"""
    value = str(order.get("fulfillmentStatus") or order.get("fulfillment_status") or order.get("orderStatus") or order.get("order_status") or order.get("status") or "").strip()
    if value.upper() in {"PAID_PENDING_SHIPMENT", "PENDING_PAYMENT_CONFIRMATION", "UNSHIPPED", "NOT_SHIPPED"}:
        return "PENDING_SHIPMENT"
    return value

def order_amount(order: dict[str, Any]) -> Any:
    """读取订单金额，作为恢复审批时的冻结字段。"""
    return order.get("totalAmount") or order.get("amount") or order.get("payAmount")

def signed_date(order: dict[str, Any]) -> str | None:
    """提取签收日期，供签收后退货窗口判断使用。"""
    value = order.get("deliveredAt") or order.get("signed_date")
    if not value:
        return None
    return str(value).split("T", 1)[0]

def is_returnable(order: dict[str, Any]) -> bool:
    """只有业务事实明确支持退货时才放行，未知值按高风险边界拦截。"""
    value = order.get("returnable")
    if isinstance(value, bool):
        return value
    items = order.get("items") or []
    if not isinstance(items, list) or not items:
        return False
    flags = [item.get("returnable") for item in items if isinstance(item, dict)]
    return len(flags) == len(items) and all(flag is True for flag in flags)

def current_user_orders_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    """从可信页面上下文读取当前用户订单候选。"""
    orders = as_order_list(context.get("currentUserOrders"))
    related_order_no = str(context.get("relatedOrderNo") or "").strip()
    if related_order_no and all(order_no(order) != related_order_no for order in orders):
        orders.append({"orderNo": related_order_no, "status": "已从当前页面带入"})
    return orders

def public_runtime_context(request: ChatRequest) -> dict[str, Any]:
    """生成可公开展示的运行时上下文摘要。"""
    context = runtime_context(request)
    return {
        "currentPage": context.get("currentPage"),
        "relatedProductId": context.get("relatedProductId"),
        "relatedOrderNo": context.get("relatedOrderNo"),
        "relatedAfterSaleNo": context.get("relatedAfterSaleNo"),
        "currentUserOrderCount": len(as_order_list(context.get("currentUserOrders"))),
    }

def find_context_order(context: dict[str, Any], target_order_no: str) -> dict[str, Any] | None:
    """在当前页面订单里查找目标订单，优先使用可信上下文。"""
    target = target_order_no.lower()
    for order in current_user_orders_from_context(context):
        if order_no(order).lower() == target:
            return order
    return None

def logistics_status_from_order(order: dict[str, Any], logistics: dict[str, Any] | None = None) -> str:
    """把订单或物流对象中的履约状态规范成统一物流状态。"""
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
