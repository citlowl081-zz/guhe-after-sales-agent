"""古河商城业务后端访问层。步骤快照优先用运行时上下文，必要时再查业务 API。"""

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

from config.settings import ecommerce_base_url
from tools.runtime_context import *

def delegated_service_headers(current_user_id: str | None) -> dict[str, str]:
    """项目 Agent 只用可信 Runtime Context 用户构造服务身份头。"""
    user_id = str(current_user_id or "").strip()
    token = os.getenv(
        "AGENT_ECOMMERCE_SERVICE_TOKEN",
        os.getenv("AGENT_SERVICE_AUTH_TOKEN", "guhe-debug-agent-service"),
    ).strip()
    if not user_id or not token:
        return {}
    return {"X-Agent-Service-Token": token, "X-Agent-User-Id": user_id}

def ecommerce_get(
    path: str,
    *,
    delegated_user_id: str | None = None,
) -> dict[str, Any] | list[Any] | None:
    """访问古河商城业务后端，调用失败时返回空结果而不是阻断项目演示。"""
    response = httpx.get(
        f"{ecommerce_base_url()}{path}",
        headers=delegated_service_headers(delegated_user_id),
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    return payload.get("data")

def order_fact_from_ecommerce(target_order_no: str, current_user_id: str) -> dict[str, Any] | None:
    """优先从业务后端读取订单事实，并校验订单归属。"""
    try:
        order = ecommerce_get(f"/api/orders/{target_order_no}", delegated_user_id=current_user_id)
    except Exception:
        return None
    return order if isinstance(order, dict) else None
