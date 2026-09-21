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

def order_status(order: dict[str, Any]) -> str:
    """把订单状态字段规范成可展示文本。"""
    value = str(order.get("fulfillmentStatus") or order.get("fulfillment_status") or order.get("orderStatus") or order.get("order_status") or order.get("status") or "").strip()
    if value.upper() in {"PAID_PENDING_SHIPMENT", "PENDING_PAYMENT_CONFIRMATION", "UNSHIPPED", "NOT_SHIPPED"}:
        return "PENDING_SHIPMENT"
    return value

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
