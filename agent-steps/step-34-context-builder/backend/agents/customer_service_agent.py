"""客服 Agent 编排层。这里连接工具、工作流、记忆或上下文模块。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat, observe_operation

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
from config.settings import load_agent_capabilities, load_guhe_env
from models.answer_client import compose_grounded_answer
from tools.runtime_context import *
from tools.planning import *
from tools.tool_runtime import *
from memory.session_memory import *
from context.context_builder import *
from policies.after_sale_policy import *
from workflows.resume import create_workflow_checkpoint, handle_resume_request

class Step34Agent:
    """Step 34：Context Builder 统一管理上下文来源。"""

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理一次 /chat 请求，并返回项目约定的公开响应结构。"""
        load_guhe_env()
        MESSAGE_COUNT_BY_SESSION[request.session_id] = MESSAGE_COUNT_BY_SESSION.get(request.session_id, 0) + 1
        message_count = MESSAGE_COUNT_BY_SESSION[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别本轮上下文目标", observe=True, intent=intent)
        memory = current_memory(request.session_id, request.runtime_user_id)
        runtime = runtime_context_facts(request)
        builder = ContextBuilder()
        builder.add(
            ContextItem(
                item_id="user-message",
                source_type="user_message",
                trust_level="untrusted",
                content=request.user_message,
                facts={"raw_user_message": request.user_message},
                conflict_group="user_claim",
                decision="只能作为用户诉求，不能作为身份、审批或业务事实。",
            )
        )
        builder.add(
            ContextItem(
                item_id="runtime-context",
                source_type="runtime_context",
                trust_level="trusted",
                content=f"登录用户 {runtime['user_id']}，会员等级 {runtime['member_level']}，风险等级 {runtime['risk_level']}。",
                facts=runtime,
                conflict_group="identity",
                decision="作为身份、会员和权限校验依据。",
            )
        )
        if memory.get("last_order_id"):
            builder.add(
                ContextItem(
                    item_id="session-memory-last-order",
                    source_type="session_memory",
                    trust_level="session",
                    content=f"本 session 最近确认过订单 {memory['last_order_id']}。",
                    facts={"last_order_id": memory["last_order_id"], "recent_intent": memory.get("recent_intent")},
                    conflict_group="order_id",
                    decision="可用于消歧，但不能覆盖 Runtime Context 和 Workflow State。",
                )
            )

        explicit_order_id = extract_order_id(request.user_message)
        page_context = runtime.get("page_context", {})
        page_order_id = page_context.get("current_order_id") or page_context.get("relatedOrderNo")
        order_id = builder.resolve_conflicts(runtime=runtime, memory=memory, page_order_id=page_order_id, explicit_order_id=explicit_order_id)
        log_guhe_event("CONTEXT_CONFLICTS_RESOLVED", "订单线索冲突已按信任等级裁决", observe=True, resolved_order_id=order_id, resolutions=builder.conflict_resolutions)
        tool_calls: list[ToolCallRecord] = []
        order: dict[str, Any] | None = None
        if order_id:
            order, order_call = load_order(order_id, str(runtime["user_id"]), runtime.get("page_context"))
            tool_calls.append(order_call)
            builder.add(
                ContextItem(
                    item_id="tool-order-observation",
                    source_type="tool_observation",
                    trust_level="verified",
                    content=order_call.observation.summary,
                    facts=order_call.observation.facts,
                    conflict_group="order_fact",
                    decision="作为订单事实依据，优先于用户文本和 Session Memory。",
                )
            )
            if order is not None:
                memory["last_order_id"] = order_no(order)

        citations: list[Citation] = []
        workflow: WorkflowState | None = None
        if intent == "refund_request":
            citations = [POLICIES["POLICY-REFUND-UNSHIPPED"]]
            policy = citations[0]
            builder.add(
                ContextItem(
                    item_id="rag-refund-policy",
                    source_type="rag_snippet",
                    trust_level="external",
                    content=policy.snippet,
                    facts=policy.model_dump(),
                    conflict_group="refund_policy",
                    decision="作为政策依据，但不替代订单事实和人工审批。",
                )
            )
            workflow = WorkflowState(
                workflow_id=f"wf-step34-{request.session_id}-{order_id or 'missing'}",
                workflow_type="unshipped_refund" if order_id else "unknown",
                status="paused" if order else "blocked",
                order_id=order_id,
                pending_action="require_human_approval" if order else "ask_clarification",
                frozen_fields={"order_id": order_id, "runtime_user_id": runtime["user_id"], "policy_ids": [policy.policy_id]},
                boundary="Workflow State 是流程事实，不能被历史消息或 Memory 覆盖。",
            )
            if order is not None:
                workflow = create_workflow_checkpoint(request, workflow, order)
            builder.add(
                ContextItem(
                    item_id="workflow-state",
                    source_type="workflow_state",
                    trust_level="verified",
                    content=f"售后流程状态 {workflow.status}，下一步 {workflow.pending_action}。",
                    facts=workflow.model_dump(),
                    conflict_group="refund_approval",
                    decision="作为流程状态依据；用户说可以退也不能覆盖它。",
                )
            )
        memory["recent_intent"] = intent
        report = builder.report()
        log_guhe_event("CONTEXT_BUILT", "Context Builder 已生成模型可见上下文", observe=True, selected_count=len(report.selected_items), model_context_count=len(report.model_context), excluded_count=len(report.excluded_items))
        answer, next_action, risk_level, needs_human_approval = self._build_answer(intent=intent, runtime=runtime, order=order, order_id=order_id, workflow=workflow, report=report)
        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=answer,
            facts={"runtime": runtime, "order": order, "context_report": report},
            citations=citations,
            workflow=workflow,
            risk_level=risk_level,
            next_action=next_action,
        )
        answer = model_result.answer
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            tool_calls=tool_calls,
            workflow=workflow,
            context_report=report,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=needs_human_approval,
            reasoning_summary=[
                "Context Builder 先给每个上下文来源标注来源和可信度，再决定能否进入模型上下文。",
                "Runtime Context 和工具 Observation 优先于用户文本；Session Memory 只辅助消歧。",
                "Workflow State 保存流程事实，不让历史消息或用户自述覆盖审批边界。",
            ],
            session_state={
                "agent_version": "step-34-context-builder",
                "message_count": message_count,
                "model_answer": model_result.model_dump(),
                "memory": memory,
                "context_builder": report.model_dump(),
                "next_gap": "来源和冲突能管理了，但上下文变长后还要处理压缩和窗口选择。",
            },
        )

    @observe_operation("resume")
    def resume(self, request: ChatResumeRequest) -> ChatResumeResponse:
        """恢复上一轮暂停的高风险售后 workflow。"""
        return handle_resume_request(request, agent_version="step-34-context-builder")

    @staticmethod
    def _build_answer(
        *,
        intent: Intent,
        runtime: dict[str, Any],
        order: dict[str, Any] | None,
        order_id: str | None,
        workflow: WorkflowState | None,
        report: ContextBuildReport,
    ) -> tuple[str, NextAction, RiskLevel, bool]:
        """根据事实、上下文和风险边界生成用户可见回答。"""
        if intent == "member_query":
            if any("member_level" in note for note in report.conflict_resolutions):
                if runtime["member_level"] == "unknown":
                    return ("系统可信上下文暂未提供可确认的会员等级，我不会按用户自称改成 VIP。", "answer_user", "low", False)
                return (f"系统可信上下文显示你是 {runtime['member_level']} 会员，我不会按用户自称改成 VIP。", "answer_user", "low", False)
            if runtime["member_level"] == "unknown":
                return ("系统可信上下文暂未提供可确认的会员等级，我会先按普通权益咨询处理。", "answer_user", "low", False)
            return (f"我会按系统确认的 {runtime['member_level']} 会员等级回答。", "answer_user", "low", False)
        if workflow is not None:
            if order is None:
                return ("退款流程缺少可信订单事实，Context Builder 不会用历史说法补这个缺口。", "ask_clarification", "high", True)
            return (
                f"{order_no(order)} 可以进入售后判断，但 workflow 仍停在 {workflow.pending_action}，不会因为历史消息说可以退就直接批准。",
                "transfer_to_human",
                "high",
                True,
            )
        if order is not None:
            return (f"Context Builder 采用可信订单事实：{order_no(order)} 当前物流状态 {logistics_status_from_order(order)}。", "answer_user", "low", False)
        if order_id and order is None:
            return ("这个订单没有通过可信订单校验，不能把用户文本或 Memory 当作事实。", "transfer_to_human", "high", True)
        return ("本轮没有触发订单或售后事实，我会按已标注来源的上下文回答。", "answer_user", "low", False)
