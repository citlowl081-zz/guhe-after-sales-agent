"""Context Builder。把多来源上下文按可信度、冲突和业务边界组装给 Agent。"""

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
from tools.tool_runtime import make_tool_call, load_order
from integrations.ecommerce_client import order_fact_from_ecommerce

def safe_page_context(raw_context: dict[str, Any] | None) -> dict[str, Any]:
    """筛选页面上下文中可给模型看的低风险字段。"""
    raw_context = raw_context or {}
    return {key: raw_context[key] for key in ["page_type", "current_order_id", "currentPage", "relatedOrderNo", "currentUserOrders"] if key in raw_context}

def runtime_context_facts(request: ChatRequest) -> dict[str, Any]:
    """把 Runtime Context 转成 Context Builder 可比较的事实项。"""
    return {
        "user_id": request.runtime_user_id,
        "nickname": request.runtime_nickname,
        "member_level": request.runtime_member_level or "unknown",
        "risk_level": request.runtime_risk_level or "unknown",
        "page_context": safe_page_context(request.runtime_context),
    }

def user_claims_vip(user_message: str) -> bool:
    """识别用户文本里的 VIP 自称，用于和系统上下文对比。"""
    return any(term in user_message for term in ["我是VIP", "我是 VIP", "我是黑卡"])

def user_claims_refund_approved(user_message: str) -> bool:
    """识别历史消息或用户文本中冒充退款已批准的说法。"""
    return any(term in user_message for term in ["客服说可以退", "上次说可以退", "已经批准", "主管同意"])

class ContextBuilder:
    """Step 34 把上下文来源、可信度和冲突处理集中在这里。"""

    def __init__(self) -> None:
        """初始化本模块对象需要的协作依赖，保持入口层只负责编排。"""
        self.selected_items: list[ContextItem] = []
        self.excluded_items: list[ContextItem] = []
        self.conflict_resolutions: list[str] = []

    def add(self, item: ContextItem) -> None:
        """向 Context Builder 加入一个带来源和可信等级的上下文项。"""
        if item.allowed_for_model:
            self.selected_items.append(item)
        else:
            self.excluded_items.append(item)

    def resolve_conflicts(self, *, runtime: dict[str, Any], memory: dict[str, Any], page_order_id: str | None, explicit_order_id: str | None) -> str | None:
        """按来源可信度解决上下文冲突，并留下公开说明。"""
        if user_claims_vip(self._user_message()) and runtime.get("member_level") != "vip":
            self.conflict_resolutions.append("member_level: 用户自称 VIP 与 Runtime Context 冲突，采用 Runtime Context。")
        memory_order_id = memory.get("last_order_id")
        chosen_order_id = explicit_order_id or page_order_id or memory_order_id
        if page_order_id and memory_order_id and page_order_id != memory_order_id and explicit_order_id is None:
            self.conflict_resolutions.append("order_id: 页面 Runtime Context 与 Session Memory 冲突，采用页面上下文。")
        if user_claims_refund_approved(self._user_message()):
            self.conflict_resolutions.append("refund_approval: 用户或历史客服说法不能覆盖 workflow state，仍需人工审批。")
        return str(chosen_order_id) if chosen_order_id else None

    def _user_message(self) -> str:
        """把用户原始消息作为低可信上下文项加入报告。"""
        for item in self.selected_items:
            if item.source_type == "user_message":
                return item.content
        return ""

    def report(self) -> ContextBuildReport:
        """输出 Context Builder 的最终选择项和冲突处理结果。"""
        ordered = sorted(self.selected_items, key=lambda item: ["trusted", "verified", "session", "external", "untrusted"].index(item.trust_level))
        model_context = [f"[{item.source_type}/{item.trust_level}] {item.content}" for item in ordered if item.allowed_for_model]
        return ContextBuildReport(
            selected_items=ordered,
            model_context=model_context,
            conflict_resolutions=self.conflict_resolutions,
            excluded_items=self.excluded_items,
        )
