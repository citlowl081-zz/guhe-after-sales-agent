"""订单字段和运行时上下文规范化工具。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.schemas import *

def as_order_list(value: Any) -> list[dict[str, Any]]:
    """把 runtime_context 中的订单列表字段统一转成订单数组。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

def order_no(order: dict[str, Any]) -> str:
    """从不同字段命名中读取订单号，保护步骤快照和业务 DTO 的边界。"""
    return str(order.get("orderNo") or order.get("order_id") or "").strip()

def order_user_id(order: dict[str, Any]) -> str:
    """读取订单归属用户，用于防止跨账号订单被工具或上下文误用。"""
    return str(order.get("userId") or order.get("user_id") or "").strip()

def order_status(order: dict[str, Any]) -> str:
    """归一化履约状态，让后续 workflow 能用稳定字段判断售后路径。"""
    value = str(order.get("fulfillmentStatus") or order.get("fulfillment_status") or order.get("orderStatus") or order.get("order_status") or order.get("status") or "").strip()
    if value.upper() in {"PAID_PENDING_SHIPMENT", "PENDING_PAYMENT_CONFIRMATION", "UNSHIPPED", "NOT_SHIPPED"}:
        return "PENDING_SHIPMENT"
    return value

def item_names(order: dict[str, Any]) -> list[str]:
    """提取订单商品名，供回答摘要、Trace 和上下文压缩使用。"""
    raw_items = order.get("items") or []
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

def logistics_status_from_order(order: dict[str, Any]) -> str:
    """从订单快照推断物流状态；没有真实物流明细时也要给出安全摘要。"""
    fulfillment = order_status(order).upper()
    if fulfillment in {"PENDING_SHIPMENT", "NOT_SHIPPED", "UNSHIPPED"}:
        return "NOT_SHIPPED"
    if fulfillment in {"SHIPPED", "IN_TRANSIT"}:
        return "IN_TRANSIT"
    if fulfillment in {"DELIVERED", "SIGNED"}:
        return "SIGNED"
    return fulfillment or "UNKNOWN"

def find_context_order(context: dict[str, Any] | None, target_order_no: str) -> dict[str, Any] | None:
    """只在当前用户上下文里查找订单，避免凭用户输入的订单号越权。"""
    if not isinstance(context, dict):
        return None
    target = target_order_no.lower()
    for order in as_order_list(context.get("currentUserOrders")):
        if order_no(order).lower() == target:
            return order
    return None
