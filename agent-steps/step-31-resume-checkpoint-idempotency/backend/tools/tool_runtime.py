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
    order = find_context_order(context or {}, order_id)
    if order is None:
        order, allowed = order_fact_from_ecommerce(order_id, runtime_user_id)
    else:
        allowed = True
    if order is None:
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "高风险售后必须先查订单事实。",
            "没有查到该订单。",
            {"order_id": order_id, "found": False},
        )
    if not allowed:
        return None, make_tool_call(
            "get_order_detail",
            {"order_id": order_id},
            "高风险售后必须绑定当前登录用户。",
            f"{order_id} 不属于当前登录用户，不能继续处理。",
            {"order_id": order_id, "found": True, "owner_matched": False},
        )
    return order, make_tool_call(
        "get_order_detail",
        {"order_id": order_id},
        "退款资格先看订单状态和支付状态。",
        f"{order_id} 履约状态 {fulfillment_status(order)}，支付状态 {payment_status(order)}。",
        {
            "order_id": order_id,
            "order_status": fulfillment_status(order),
            "payment_status": payment_status(order),
            "fulfillment_status": fulfillment_status(order),
            "owner_matched": True,
        },
    )

def load_logistics(order: dict[str, Any], runtime_user_id: str) -> ToolCallRecord:
    """读取物流事实，并写入可观察的工具调用结果。"""
    logistics = logistics_fact_from_ecommerce(order_no(order), runtime_user_id)
    status = logistics_status_from_order(order, logistics)
    return make_tool_call(
        "get_order_logistics",
        {"order_id": order_no(order)},
        "退款资格还要看物流是否已经出库、发货或签收。",
        f"{order_no(order)} 物流状态 {status}。",
        {"order_id": order_no(order), "logistics_status": status, "signed_date": signed_date(order)},
    )
