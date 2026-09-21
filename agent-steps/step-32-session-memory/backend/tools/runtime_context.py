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
    """读取支付状态，供退款资格和恢复审批复核使用。"""
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

def order_status(order: dict[str, Any]) -> str:
    """把订单状态字段规范成可展示文本。"""
    value = str(order.get("fulfillmentStatus") or order.get("fulfillment_status") or order.get("orderStatus") or order.get("order_status") or order.get("status") or "").strip()
    if value.upper() in {"PAID_PENDING_SHIPMENT", "PENDING_PAYMENT_CONFIRMATION", "UNSHIPPED", "NOT_SHIPPED"}:
        return "PENDING_SHIPMENT"
    return value

def signed_date(order: dict[str, Any]) -> str | None:
    """提取签收日期，保留前一步的退货窗口判断能力。"""
    value = order.get("deliveredAt") or order.get("signed_date")
    if not value:
        return None
    return str(value).split("T", 1)[0]

def is_returnable(order: dict[str, Any]) -> bool:
    """只有业务事实明确支持退货时才放行，Memory 不能补造商品属性。"""
    value = order.get("returnable")
    if isinstance(value, bool):
        return value
    items = order.get("items") or []
    if not isinstance(items, list) or not items:
        return False
    flags = [item.get("returnable") for item in items if isinstance(item, dict)]
    return len(flags) == len(items) and all(flag is True for flag in flags)

def item_names(order: dict[str, Any]) -> list[str]:
    """提取订单商品名，用于会话记忆和回答消歧。"""
    raw_items = order.get("items") or order.get("itemSummary") or []
    if isinstance(raw_items, str):
        return [raw_items]
    if not isinstance(raw_items, list):
        return []
    names: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            name = item.get("productName") or item.get("name")
            if name:
                names.append(str(name))
        elif item:
            names.append(str(item))
    return names

def logistics_status_from_order(order: dict[str, Any], logistics: dict[str, Any] | None = None) -> str:
    """把订单或物流对象中的履约状态规范成统一物流状态。"""
    if isinstance(logistics, dict) and logistics.get("status"):
        return str(logistics.get("status"))
    fulfillment = order_status(order).upper()
    if fulfillment in {"PENDING_SHIPMENT", "NOT_SHIPPED", "UNSHIPPED"}:
        return "NOT_SHIPPED"
    if fulfillment in {"SHIPPED", "IN_TRANSIT"}:
        return "IN_TRANSIT"
    if fulfillment in {"DELIVERED", "SIGNED"}:
        return "SIGNED"
    return fulfillment or "UNKNOWN"

def find_context_order(context: dict[str, Any] | None, target_order_no: str) -> dict[str, Any] | None:
    """在当前页面订单里查找目标订单，优先使用可信上下文。"""
    if not isinstance(context, dict):
        return None
    target = target_order_no.lower()
    for order in as_order_list(context.get("currentUserOrders")):
        if order_no(order).lower() == target:
            return order
    return None

def runtime_context(request: ChatRequest) -> dict[str, Any]:
    """整理给 workflow 使用的可信运行时上下文。"""
    context = dict(request.runtime_context or {})
    context.update(
        {
            "runtime_user_id": request.runtime_user_id,
            "runtime_member_level": request.runtime_member_level,
            "runtime_risk_level": request.runtime_risk_level,
        }
    )
    return context
