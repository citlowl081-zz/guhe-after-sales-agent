"""HITL 恢复与 checkpoint 层，保护高风险售后动作的恢复、复核和幂等。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.schemas import *
from integrations.ecommerce_client import order_fact_from_ecommerce
from state.session_state import SUBMITTED_ACTIONS, WORKFLOW_CHECKPOINTS
from tools.runtime_context import logistics_status_from_order, order_no, order_status

def build_resume_token(session_id: str, workflow_id: str, order_id: str | None) -> str:
    """生成 HITL 恢复令牌，说明高风险 workflow 不能靠普通聊天继续执行。"""
    return f"resume-{session_id}-{workflow_id}-{order_id or 'missing'}"


def freeze_workflow_fields(request: ChatRequest, order: dict[str, Any] | None) -> dict[str, Any]:
    """冻结审批前的关键业务事实，恢复时用来检查订单状态是否漂移。"""
    return {
        "runtime_user_id": request.runtime_user_id,
        "order_id": order_no(order) if order else None,
        "order_status": order_status(order) if order else None,
        "logistics_status": logistics_status_from_order(order) if order else None,
    }


def business_recheck(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """恢复 workflow 前复核冻结事实，防止审批期间订单状态变化后继续执行。"""
    frozen = checkpoint["frozen_fields"]
    order_id = frozen.get("order_id")
    current_order = order_fact_from_ecommerce(order_id, str(frozen["runtime_user_id"])) if order_id else None
    if current_order is None:
        current_order = checkpoint.get("order_snapshot")
    if current_order is None:
        return {"passed": False, "reason": "order_not_found", "mismatches": {"order_id": {"frozen": order_id, "current": None}}}
    mismatches: dict[str, Any] = {}
    current_values = {
        "order_status": order_status(current_order),
        "logistics_status": logistics_status_from_order(current_order),
    }
    for field in ("order_status", "logistics_status"):
        if frozen.get(field) != current_values.get(field):
            mismatches[field] = {"frozen": frozen.get(field), "current": current_values.get(field)}
    return {"passed": not mismatches, "reason": None if not mismatches else "business_fact_drift", "mismatches": mismatches}
