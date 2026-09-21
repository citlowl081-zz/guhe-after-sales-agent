"""HITL 人工审批边界。普通聊天不能伪装成人工审批结果。"""

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

def build_approval_request(
    workflow_id: str,
    workflow_type: Literal["unshipped_refund", "received_return", "unknown"],
    assessment: HighRiskAssessment,
    submitted_by: str,
) -> ApprovalRequest:
    """创建待人工审批申请，Agent 只提交申请不批准结果。"""
    action_label = "退款" if workflow_type == "unshipped_refund" else "退货"
    return ApprovalRequest(
        approval_id="appr-{0}".format(workflow_id),
        workflow_id=workflow_id,
        status="pending",
        required_role="after_sale_manager",
        submitted_by=submitted_by,
        risk_summary="订单 {0} 可发起{1}申请，但必须由售后主管人工审批。".format(assessment.order_id, action_label),
        decision_options=["approved", "rejected", "needs_more_info"],
        boundary="HITL 只创建待审批状态；普通聊天不能当审批，必须通过 /chat/resume 恢复。",
    )

def is_chat_approval_claim(user_message: str) -> bool:
    """识别普通聊天里的审批冒充话术，并阻断它进入恢复通道。"""
    return any(term in user_message for term in ["审批通过", "主管同意", "我批准", "已经批准", "直接通过"])
