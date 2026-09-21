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

def load_owned_order(order_id: str, runtime_user_id: str, context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, ToolCallRecord]:
    """读取当前用户拥有的订单，未通过归属校验时不写入记忆。"""
    order = find_context_order(context, order_id)
    if order is None:
        order, allowed = order_fact_from_ecommerce(order_id, runtime_user_id)
    else:
        owner = order_user_id(order)
        allowed = not owner or owner == runtime_user_id
    if order is None:
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "只有查到可信订单事实，Memory 才能记最近订单。",
            "没有查到该订单。",
            {"order_id": order_id, "found": False},
        )
    if not allowed:
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "最近订单必须属于当前登录用户，不能把越权订单写进记忆。",
            f"{order_id} 不属于当前登录用户。",
            {"order_id": order_id, "found": True, "owner_matched": False},
        )
    return order, make_tool_call(
        "get_order_detail",
        {"order_id": order_id},
        "把被业务系统确认过的订单作为短期会话记忆。",
        f"{order_id} 当前订单状态 {order_status(order)}，物流状态 {logistics_status_from_order(order, logistics_fact_from_ecommerce(order_id, runtime_user_id))}。",
        {
            "order_id": order_id,
            "found": True,
            "owner_matched": True,
            "order_status": order_status(order),
            "logistics_status": logistics_status_from_order(order),
        },
    )

def build_logistics_call(order: dict[str, Any], runtime_user_id: str) -> ToolCallRecord:
    """补充物流事实，不把原始物流详情写入公开响应。"""
    order_id = order_no(order)
    logistics = logistics_fact_from_ecommerce(order_id, runtime_user_id)
    return make_tool_call(
        "get_order_logistics",
        {"order_id": order_id},
        "售后 workflow 需要物流状态作为资格判断证据。",
        f"{order_id} 当前物流状态 {logistics_status_from_order(order, logistics)}。",
        {
            "order_id": order_id,
            "logistics_status": logistics_status_from_order(order, logistics),
        },
    )
