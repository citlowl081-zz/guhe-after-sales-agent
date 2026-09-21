"""工具执行与工具调用记录。所有业务事实都要留下可观察的 ToolCallRecord。"""

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
from integrations.ecommerce_client import *
from tools.runtime_context import *

def make_tool_call(tool_name: str, arguments: dict[str, Any], reason: str, summary: str, facts: dict[str, Any]) -> ToolCallRecord:
    """构造公开 ToolCallRecord，让工具事实和观察结果可追踪。"""
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

def load_order_for_runtime_context(order_id: str, context: RuntimeContextView) -> tuple[dict[str, Any] | None, ToolCallRecord, dict[str, Any]]:
    """按系统侧 Runtime Context 校验订单访问权限。"""
    runtime_user_id = str(context.system_only["user_id"])
    page_context = context.trusted_for_model.get("page_context", {})
    order = find_context_order(page_context if isinstance(page_context, dict) else {}, order_id)
    if order is None:
        order = order_fact_from_ecommerce(order_id, runtime_user_id)
    if order is None:
        decision = {"allowed": False, "reason": "order_not_found"}
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "页面上下文或用户问题提到订单时，必须先做系统侧订单校验。",
            "没有查到该订单。",
            {"order_id": order_id, "found": False},
        ), decision
    owner = order_user_id(order)
    if owner and owner != runtime_user_id:
        decision = {"allowed": False, "reason": "order_owner_mismatch"}
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "Runtime Context 里的 user_id 是权限校验依据，不能被用户自述覆盖。",
            f"{order_id} 不属于当前登录用户。",
            {"order_id": order_id, "found": True, "owner_matched": False},
        ), decision
    decision = {"allowed": True, "reason": "owner_matched"}
    return order, make_tool_call(
        "get_order_detail",
        {"order_id": order_id},
        "订单读取必须绑定 Runtime Context 里的可信用户身份。",
        f"{order_id} 属于当前登录用户，订单状态 {order_status(order)}。",
        {
            "order_id": order_id,
            "found": True,
            "owner_matched": True,
            "order_status": order_status(order),
            "logistics_status": logistics_status_from_order(order),
        },
    ), decision
