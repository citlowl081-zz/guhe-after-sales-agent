"""售后政策和高风险资格判断。这里只判断边界，不直接执行退款。"""

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
from config.settings import TODAY
from tools.runtime_context import *
from tools.tool_runtime import make_tool_call

POLICIES: dict[str, Citation] = {
    "POLICY-REFUND-UNSHIPPED": Citation(
        citation_id="c-refund-unshipped",
        source_title="古河商城公司未发货退款 SOP",
        source_path="knowledge/after_sale_policy.md",
        policy_id="POLICY-REFUND-UNSHIPPED",
        snippet="已支付且未出库、未发货的订单，可以发起退款申请；资金类动作必须进入售后审批。",
    ),
    "POLICY-RETURN-RECEIVED": Citation(
        citation_id="c-return-received",
        source_title="古河商城公司签收后退货 SOP",
        source_path="knowledge/after_sale_policy.md",
        policy_id="POLICY-RETURN-RECEIVED",
        snippet="签收后 7 天内、商品支持退货且原因符合规则时，可以发起退货申请；不得自动批准。",
    ),
}

def retrieve_policy(action_type: Literal["refund", "return", "unknown"]) -> tuple[list[Citation], ToolCallRecord]:
    """检索售后政策依据，给资格判断提供 citation。"""
    if action_type == "return":
        citations = [POLICIES["POLICY-RETURN-RECEIVED"]]
        query = "签收后退货政策"
    else:
        citations = [POLICIES["POLICY-REFUND-UNSHIPPED"]]
        query = "未发货退款政策"
    return citations, make_tool_call(
        "retrieve_after_sale_policy",
        {"query": query},
        "资格判断必须拿到古河商城公司的售后政策依据。",
        "已检索到售后政策依据。",
        {"policy_ids": [citation.policy_id for citation in citations]},
    )

def assess_refund_boundary(order: dict[str, Any] | None, citations: list[Citation], action_type: Literal["refund", "return", "unknown"]) -> HighRiskAssessment:
    """综合订单、物流和政策判断售后资格与高风险边界。"""
    if order is None:
        return HighRiskAssessment(
            action_type=action_type,
            order_id=None,
            eligibility_status="blocked",
            risk_level="high",
            needs_human_approval=True,
            evidence_checklist=["订单归属未通过"],
            policy_basis=citations,
            reasons=["当前没有拿到可信订单事实，不能执行退款、退货或取消。"],
            blocked_write_actions=["create_refund", "approve_refund", "cancel_order", "create_compensation"],
        )

    evidence = ["订单状态", "物流状态", "售后政策依据"]
    reasons: list[str] = []
    eligible = False
    if action_type == "refund":
        eligible = (
            fulfillment_status(order) == "PENDING_SHIPMENT"
            and payment_status(order) == "PAID"
            and logistics_status_from_order(order) == "NOT_SHIPPED"
        )
        if eligible:
            reasons.append("订单已支付、未出库、未发货，只能判断为可发起退款申请。")
        else:
            reasons.append("订单已经出库、发货或状态不满足未发货退款条件，不能按一句话直接退款。")
    elif action_type == "return":
        days_since_signed = signed_days(order)
        within_window = days_since_signed is not None and days_since_signed <= 7
        eligible = (
            fulfillment_status(order) == "DELIVERED"
            and logistics_status_from_order(order) == "SIGNED"
            and is_returnable(order)
            and within_window
        )
        evidence.extend(["签收时间", "商品可退属性", "退货原因"])
        if eligible:
            reasons.append("订单已签收 {0} 天，商品支持退货，原因符合签收后退货基础条件。".format(days_since_signed))
        else:
            if fulfillment_status(order) != "DELIVERED" or logistics_status_from_order(order) != "SIGNED":
                reasons.append("订单还没有签收，不能走签收后退货流程。")
            elif not is_returnable(order):
                reasons.append("该商品属于不支持无理由退货的商品，不能自动进入退货申请。")
            elif not within_window:
                reasons.append("订单签收已超过 7 天，不能按七天无理由直接进入退货申请。")
            else:
                reasons.append("订单或商品状态不满足签收后退货的基础条件。")
    else:
        reasons.append("用户没有说清楚是退款还是退货，需要先澄清售后类型。")

    return HighRiskAssessment(
        action_type=action_type,
        order_id=order_no(order),
        eligibility_status="eligible_for_application" if eligible else "not_eligible",
        risk_level="high",
        needs_human_approval=True,
        evidence_checklist=evidence,
        policy_basis=citations,
        reasons=reasons,
        blocked_write_actions=["create_refund", "approve_refund", "cancel_order", "create_compensation"],
    )

def signed_days(order: dict[str, Any]) -> int | None:
    """计算签收距项目日期的天数，用于七天无理由判断。"""
    signed_at = signed_date(order)
    if not signed_at:
        return None
    return (TODAY - date.fromisoformat(str(signed_at))).days
