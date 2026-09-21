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

def load_order(order_id: str, runtime_user_id: str, context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, ToolCallRecord]:
    """读取并校验订单事实，失败时返回安全的工具观察记录。"""
    order = find_context_order(context, order_id)
    if order is None:
        order = order_fact_from_ecommerce(order_id, runtime_user_id)
    if order is None:
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "Context Builder 只接收结构化工具观察，不接收原始业务 payload。",
            "没有查到该订单。",
            {"order_id": order_id, "found": False},
        )
    owner = order_user_id(order)
    if owner and owner != runtime_user_id:
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "订单事实必须和 Runtime Context 里的可信用户匹配。",
            f"{order_id} 不属于当前登录用户。",
            {"order_id": order_id, "found": True, "owner_matched": False},
        )
    return order, make_tool_call(
        "get_order_detail",
        {"order_id": order_id},
        "工具结果进入 Context Builder 前先压缩成 Observation。",
        f"{order_id} 订单状态 {order_status(order)}，物流状态 {logistics_status_from_order(order)}。",
        {
            "order_id": order_id,
            "found": True,
            "owner_matched": True,
            "order_status": order_status(order),
            "logistics_status": logistics_status_from_order(order),
            "amount": order_amount(order),
        },
    )
