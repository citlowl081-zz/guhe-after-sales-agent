"""可信 Runtime Context 构造层。用户自述不能覆盖系统上下文。"""

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
from tools.runtime_context import *
from tools.planning import extract_order_id
from tools.tool_runtime import make_tool_call
from integrations.ecommerce_client import order_fact_from_ecommerce

def user_claims_vip(user_message: str) -> bool:
    """识别用户文本里的 VIP 自称，用于和系统上下文对比。"""
    return any(term in user_message for term in ["我是VIP", "我是 VIP", "我是黑卡", "我是尊贵会员"])

def safe_page_context(raw_context: dict[str, Any] | None) -> dict[str, Any]:
    """筛选页面上下文中可给模型看的低风险字段。"""
    raw_context = raw_context or {}
    allowed: dict[str, Any] = {}
    for key in ["page_type", "current_order_id", "current_product_id", "currentPage", "relatedOrderNo", "relatedProductId", "currentUserOrders"]:
        if key in raw_context:
            allowed[key] = raw_context[key]
    return allowed

def build_runtime_context(request: ChatRequest) -> RuntimeContextView:
    """构造可信 Runtime Context 视图，区分模型可见和系统专用字段。"""
    authenticated = bool(request.runtime_user_id)
    member_level = request.runtime_member_level or "unknown"
    risk_level = request.runtime_risk_level or "unknown"
    page_context = safe_page_context(request.runtime_context)
    permissions = ["read_own_order", "ask_after_sale"] if authenticated else []
    conflict_notes: list[str] = []

    if user_claims_vip(request.user_message) and member_level != "vip":
        conflict_notes.append("用户在文本里自称 VIP，但系统登录态没有确认 VIP 身份。")
    if member_level == "vip" and "vip_service" not in permissions:
        permissions.append("vip_service")

    trusted_for_model = {
        "authenticated": authenticated,
        "nickname": request.runtime_nickname,
        "member_level": member_level,
        "page_context": page_context,
    }
    system_only = {
        "user_id": request.runtime_user_id,
        "risk_level": risk_level,
        "permissions": permissions,
    }
    return RuntimeContextView(
        trusted_for_model=trusted_for_model,
        system_only=system_only,
        conflict_notes=conflict_notes,
        permission_decision={"allowed": True, "reason": "本轮尚未触发需要订单归属校验的动作。"},
    )

def resolve_order_from_runtime_context(request: ChatRequest, context: RuntimeContextView) -> str | None:
    """用可信页面订单或用户消息解析本轮目标订单。"""
    explicit_order_id = extract_order_id(request.user_message)
    if explicit_order_id:
        return explicit_order_id
    if any(term in request.user_message for term in ["当前订单", "这个订单"]):
        page_context = context.trusted_for_model.get("page_context", {})
        current_order_id = page_context.get("current_order_id") or page_context.get("relatedOrderNo")
        return str(current_order_id) if current_order_id else None
    return None
