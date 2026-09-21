"""业务工具执行层，负责生成工具事实和可观察记录。"""

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
from rag.knowledge import *
from tools.runtime_context import *
from tools.planning import estimate_tokens
from integrations.ecommerce_client import order_fact_from_ecommerce
from observability.trace import trace_store

def get_order_detail(
    order_id: str | None,
    runtime_user_id: str,
    runtime_context: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, ToolCallTrace]:
    """执行订单详情工具，并把工具开始、成功或失败写入公开 Trace。"""
    arguments = {"order_id": order_id}
    if order_id is None:
        return None, ToolCallTrace(
            tool_name="get_order_detail",
            arguments=arguments,
            output_summary="缺少订单号，不能查询订单。",
            status="error",
            error_type="missing_order_id",
            next_action="ask_clarification",
        )
    order = find_context_order(runtime_context, order_id) or order_fact_from_ecommerce(order_id, runtime_user_id)
    if order is None or (order_user_id(order) and order_user_id(order) != runtime_user_id):
        return None, ToolCallTrace(
            tool_name="get_order_detail",
            arguments=arguments,
            output_summary=f"订单 {order_id} 没有通过当前用户归属校验。",
            status="error",
            error_type="owner_mismatch",
            risk_level="medium",
            next_action="transfer_to_human",
        )
    summary = f"订单 {order_no(order)} 状态 {order_status_label(order)}，物流 {logistics_status_label(order)}，商品 {item_names(order)[0]}。"
    return order, ToolCallTrace(tool_name="get_order_detail", arguments=arguments, output_summary=summary, status="success", risk_level="low")

def get_order_logistics(order: dict[str, Any]) -> ToolCallTrace:
    """执行物流查询工具，把参数、状态和摘要写成可观测事件。"""
    return ToolCallTrace(
        tool_name="get_order_logistics",
        arguments={"order_id": order_no(order)},
        output_summary=f"物流状态 {logistics_status_label(order)}，订单状态 {order_status_label(order)}。",
        status="success",
        risk_level="low",
        next_action="answer_user",
    )
