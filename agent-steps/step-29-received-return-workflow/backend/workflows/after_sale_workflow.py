"""售后工作流层。用显式节点固定高风险售后流程顺序。"""

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
from guhe_runtime.guhe_logging import log_guhe_event
from pydantic import BaseModel, Field

from api.schemas import *
from tools.runtime_context import runtime_context
from tools.planning import classify_intent, extract_order_id
from tools.tool_runtime import load_order, load_logistics, make_tool_call
from policies.after_sale_policy import retrieve_policy, assess_refund_boundary

class AfterSaleWorkflowState(TypedDict, total=False):
    session_id: str
    runtime_user_id: str
    runtime_context: dict[str, Any]
    user_message: str
    workflow_id: str
    workflow_type: Literal["unshipped_refund", "received_return", "unknown"]
    action_type: Literal["refund", "return", "unknown"]
    order_id: str | None
    order: dict[str, Any] | None
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    assessment: HighRiskAssessment | None
    status: Literal["running", "completed", "blocked"]
    current_node: str
    pending_action: str
    node_history: list[str]
    answer: str

class AfterSaleWorkflow:
    """Step 29 的未发货退款 + 签收后退货 StateGraph。

    项目重点：签收后退货不是“已签收就能退”，还要看签收时间、商品属性、
    用户原因和政策依据。HITL 审批和恢复仍然不在这一步开放。
    """

    def __init__(self) -> None:
        """初始化本模块对象需要的协作依赖，保持入口层只负责编排。"""
        self.graph = self._build_graph()

    def run(self, request: ChatRequest) -> AfterSaleWorkflowState:
        """初始化本轮工作流状态，并交给固定节点执行。"""
        order_id = extract_order_id(request.user_message)
        intent = classify_intent(request.user_message)
        action_type: Literal["refund", "return", "unknown"] = "return" if intent == "return_request" else "refund" if intent == "refund_request" else "unknown"
        workflow_id = "wf-step29-{0}-{1}".format(request.session_id, order_id or "missing")
        initial_state: AfterSaleWorkflowState = {
            "session_id": request.session_id,
            "runtime_user_id": request.runtime_user_id,
            "runtime_context": runtime_context(request),
            "user_message": request.user_message,
            "workflow_id": workflow_id,
            "workflow_type": "unknown",
            "action_type": action_type,
            "order_id": order_id,
            "order": None,
            "citations": [],
            "tool_calls": [],
            "assessment": None,
            "status": "running",
            "current_node": "classify_after_sale_intent",
            "pending_action": "run_workflow",
            "node_history": [],
            "answer": "",
        }
        return self.graph.invoke(initial_state)

    def _build_graph(self):
        """注册 LangGraph 节点和条件边，让售后流程顺序由代码固定。"""
        graph = StateGraph(AfterSaleWorkflowState)
        graph.add_node("classify_after_sale_intent", self._classify_after_sale_intent)
        graph.add_node("load_order", self._load_order)
        graph.add_node("load_logistics", self._load_logistics)
        graph.add_node("retrieve_policy", self._retrieve_policy)
        graph.add_node("check_eligibility", self._check_eligibility)
        graph.add_node("stop_before_submission", self._stop_before_submission)
        graph.set_entry_point("classify_after_sale_intent")
        graph.add_conditional_edges(
            "classify_after_sale_intent",
            self._route_after_classify,
            {"load_order": "load_order", "stop_before_submission": "stop_before_submission"},
        )
        graph.add_conditional_edges(
            "load_order",
            self._route_after_order,
            {"load_logistics": "load_logistics", "stop_before_submission": "stop_before_submission"},
        )
        graph.add_edge("load_logistics", "retrieve_policy")
        graph.add_edge("retrieve_policy", "check_eligibility")
        graph.add_edge("check_eligibility", "stop_before_submission")
        graph.add_edge("stop_before_submission", END)
        return graph.compile()

    def _classify_after_sale_intent(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """把用户售后诉求映射成工作流类型。"""
        workflow_type: Literal["unshipped_refund", "received_return", "unknown"]
        if state["action_type"] == "refund":
            workflow_type = "unshipped_refund"
        elif state["action_type"] == "return":
            workflow_type = "received_return"
        else:
            workflow_type = "unknown"
        return self._complete(state, "classify_after_sale_intent", {"workflow_type": workflow_type})

    def _load_order(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """工作流节点：读取订单事实，不满足归属时停止。"""
        order, order_call = load_order(str(state["order_id"]), state["runtime_user_id"], state.get("runtime_context") or {})
        updates: dict[str, Any] = {"order": order, "tool_calls": [*state["tool_calls"], order_call]}
        if order is None:
            updates.update(
                {
                    "status": "blocked",
                    "pending_action": "transfer_to_human",
                    "answer": "订单事实或归属没有通过校验，售后流程不能继续。",
                }
            )
        return self._complete(state, "load_order", updates)

    def _load_logistics(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """工作流节点：读取物流状态，补齐资格判断证据。"""
        order = state["order"]
        if order is None:
            return self._complete(state, "load_logistics")
        return self._complete(state, "load_logistics", {"tool_calls": [*state["tool_calls"], load_logistics(order, state["runtime_user_id"])]})

    def _retrieve_policy(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """工作流节点：读取售后政策依据。"""
        citations, policy_call = retrieve_policy(state["action_type"])
        return self._complete(
            state,
            "retrieve_policy",
            {"citations": citations, "tool_calls": [*state["tool_calls"], policy_call]},
        )

    def _check_eligibility(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """工作流节点：把事实和政策收口为资格判断。"""
        assessment = assess_refund_boundary(state.get("order"), state["citations"], state["action_type"])
        tool_call = make_tool_call(
            "check_after_sale_boundary",
            {"order_id": state.get("order_id"), "action_type": state["action_type"]},
            "StateGraph 固定节点流后，在资格节点统一收口。",
            "已完成售后资格判断，但还没有进入申请提交或人工审批。",
            {
                "eligibility_status": assessment.eligibility_status,
                "needs_human_approval": assessment.needs_human_approval,
                "blocked_write_actions": assessment.blocked_write_actions,
            },
        )
        return self._complete(state, "check_eligibility", {"assessment": assessment, "tool_calls": [*state["tool_calls"], tool_call]})

    def _stop_before_submission(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """工作流节点：在提交、审批或恢复前公开边界并停止。"""
        assessment = state.get("assessment")
        if assessment is None:
            assessment = HighRiskAssessment(
                action_type=state["action_type"],
                order_id=state.get("order_id"),
                eligibility_status="needs_clarification",
                risk_level="high",
                needs_human_approval=True,
                evidence_checklist=[],
                policy_basis=[],
                reasons=["当前版本支持未发货退款和签收后退货；缺少订单号或售后类型不明确时，流程停在边界说明。"],
                blocked_write_actions=["create_refund", "approve_refund", "cancel_order", "create_compensation"],
            )
        status: Literal["completed", "blocked"] = "blocked" if assessment.eligibility_status in {"blocked", "needs_clarification"} else "completed"
        if state.get("workflow_type") == "received_return" and assessment.eligibility_status == "eligible_for_application":
            answer = (
                "订单 {0} 已完成签收时间、商品可退属性、退货原因和政策依据检查，"
                "只判断为可以准备退货申请。当前还不是退货成功，也没有人工批准。"
            ).format(state.get("order_id"))
        elif state.get("workflow_type") == "received_return":
            answer = "订单 {0} 不满足签收后退货条件：{1}".format(state.get("order_id"), assessment.reasons[0])
        elif state.get("workflow_type") != "unshipped_refund":
            answer = "请先说明订单号和售后类型。高风险售后必须进入固定流程后才能继续。"
        elif assessment.eligibility_status == "eligible_for_application":
            answer = (
                "订单 {0} 已支付、未出库、未发货，符合发起未发货退款申请的基础条件。"
                "但这还不是退款成功，也没有提交审批；当前只停在可准备申请的边界。"
            ).format(state.get("order_id"))
        else:
            answer = "订单 {0} 不满足未发货退款条件：{1}".format(state.get("order_id"), assessment.reasons[0])
        if assessment.eligibility_status == "eligible_for_application" and state.get("workflow_type") == "received_return":
            pending_action = "prepare_return_application"
        elif assessment.eligibility_status == "eligible_for_application":
            pending_action = "prepare_refund_application"
        else:
            pending_action = "explain_boundary"
        return self._complete(
            state,
            "stop_before_submission",
            {
                "assessment": assessment,
                "status": status,
                "pending_action": pending_action,
                "answer": state.get("answer") or answer,
            },
        )

    @staticmethod
    def _route_after_classify(state: AfterSaleWorkflowState) -> str:
        """根据意图和订单号决定继续查订单还是停止澄清。"""
        if state.get("order_id") and state.get("workflow_type") in {"unshipped_refund", "received_return"}:
            return "load_order"
        return "stop_before_submission"

    @staticmethod
    def _route_after_order(state: AfterSaleWorkflowState) -> str:
        """根据订单事实是否存在决定继续查物流还是停止。"""
        if state.get("order") is None:
            return "stop_before_submission"
        return "load_logistics"

    @staticmethod
    def _complete(state: AfterSaleWorkflowState, node: str, updates: dict[str, Any] | None = None) -> dict[str, Any]:
        """统一写入当前节点和节点历史，保持 workflow 可观察。"""
        log_guhe_event("WORKFLOW_NODE", "退款/退货节点执行完成", observe=True, node=node, workflow_id=state.get("workflow_id"))
        return {
            **(updates or {}),
            "current_node": node,
            "node_history": [*state.get("node_history", []), node],
        }

WORKFLOW = AfterSaleWorkflow()
