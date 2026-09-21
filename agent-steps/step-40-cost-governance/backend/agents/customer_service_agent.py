"""客服 Agent 编排层，连接本步新增能力和公开响应。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import observe_chat, observe_operation

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
from config.settings import TRACE_SCHEMA_VERSION, load_agent_capabilities, load_guhe_env
from models.answer_client import compose_grounded_answer
from state.session_state import *
from rag.knowledge import *
from tools.runtime_context import *
from tools.planning import *
from integrations.ecommerce_client import *
from observability.trace import *
from tools.tool_runtime import *
from cost.governance import *
from workflows.resume import build_resume_token, business_recheck, freeze_workflow_fields

class Step40Agent:
    """Step 40：成本治理，把轻路径、重路径和缓存命中变成可观察信号。"""

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """编排本步 Agent 主链路，串联意图、上下文、工具、Trace、Eval 或成本治理能力。"""
        load_guhe_env()
        MESSAGE_COUNT_BY_SESSION[request.session_id] = MESSAGE_COUNT_BY_SESSION.get(request.session_id, 0) + 1
        message_count = MESSAGE_COUNT_BY_SESSION[request.session_id]
        intent = classify_intent(request.user_message)
        order_id = extract_order_id(request.user_message)
        citations: list[Citation] = []
        tool_calls: list[ToolCallTrace] = []
        workflow: dict[str, Any] | None = None
        cache_hit = False

        trace_store.add(
            request.session_id,
            "runtime_context_built",
            {
                "session_id": request.session_id,
                "runtime_user_id": request.runtime_user_id,
                "member_level": request.runtime_member_level or "unknown",
                "risk_level": request.runtime_risk_level or "unknown",
            },
        )
        trace_store.add(
            request.session_id,
            "context_built",
            {
                "session_id": request.session_id,
                "intent": intent,
                "sources": ["user_message", "runtime_context", "session_state"],
                "estimated_tokens": estimate_tokens(request.user_message),
            },
        )

        if intent == "security_request":
            trace_store.add(
                request.session_id,
                "prompt_security_blocked",
                {"session_id": request.session_id, "intent": intent, "risk_level": "high", "status": "blocked"},
            )
            answer = "我不能提供系统提示词、hidden reasoning、工具细节或内部策略。"
            risk_level: RiskLevel = "high"
            next_action: NextAction = "answer_user"
            needs_human_approval = False
        elif "发票" in request.user_message:
            cache_key = "faq:invoice_issue"
            cached = COMMON_HIT_CACHE.get(cache_key)
            if cached:
                cache_hit = True
                answer = str(cached["answer"])
                citations = [INVOICE_FAQ]
            else:
                answer = "电子发票通常在订单完成后 24 小时内开具，你可以在订单详情页查看和下载。"
                citations = [INVOICE_FAQ]
                COMMON_HIT_CACHE[cache_key] = {"answer": answer, "citation": "invoice_issue"}
                trace_store.add(
                    request.session_id,
                    "rag_pre_retrieved",
                    {
                        "session_id": request.session_id,
                        "hit_count": 1,
                        "retrieval_stage": "pre_retrieval",
                        "policy_id": "invoice_issue",
                    },
                )
            risk_level = "low"
            next_action = "answer_user"
            needs_human_approval = False
        elif intent in {"order_query", "refund_request"}:
            trace_store.add(
                request.session_id,
                "tool_started",
                {"session_id": request.session_id, "tool_name": "get_order_detail", "order_id": order_id},
            )
            order, detail_call = get_order_detail(order_id, request.runtime_user_id, request.runtime_context)
            tool_calls.append(detail_call)
            trace_store.add(
                request.session_id,
                "tool_finished",
                {
                    "session_id": request.session_id,
                    "tool_name": detail_call.tool_name,
                    "order_id": order_id,
                    "status": detail_call.status,
                    "risk_level": detail_call.risk_level,
                    "next_action": detail_call.next_action,
                },
            )
            if intent == "order_query" and order:
                logistics_call = get_order_logistics(order)
                tool_calls.append(logistics_call)
                trace_store.add(
                    request.session_id,
                    "tool_finished",
                    {
                        "session_id": request.session_id,
                        "tool_name": logistics_call.tool_name,
                        "order_id": order_id,
                        "status": logistics_call.status,
                        "risk_level": logistics_call.risk_level,
                    },
                )
                answer = f"我帮你查到了，订单 {order_no(order)} 目前{order_status_label(order)}，物流状态是{logistics_status_label(order)}。"
                risk_level = "low"
                next_action = "answer_user"
                needs_human_approval = False
            elif intent == "refund_request":
                citations.append(REFUND_POLICY)
                trace_store.add(
                    request.session_id,
                    "rag_pre_retrieved",
                    {
                        "session_id": request.session_id,
                        "hit_count": 1,
                        "retrieval_stage": "pre_retrieval",
                        "policy_id": "refund_before_shipping",
                    },
                )
                workflow = {
                    "workflow_id": f"wf-{request.session_id}",
                    "workflow_type": "unshipped_refund",
                    "status": "paused",
                    "pending_action": "require_approval",
                    "order_id": order_no(order) if order else order_id,
                    "resume_token": build_resume_token(request.session_id, f"wf-{request.session_id}", order_id),
                    "approval_id": f"appr-{request.session_id}",
                    "idempotency_key": f"hitl:{request.session_id}:{order_id}",
                    "frozen_fields": freeze_workflow_fields(request, order),
                }
                WORKFLOW_CHECKPOINTS[(request.session_id, workflow["workflow_id"])] = {
                    "workflow": workflow,
                    "frozen_fields": workflow["frozen_fields"],
                    "resume_token": workflow["resume_token"],
                    "idempotency_key": workflow["idempotency_key"],
                    "citations": [citation.model_dump() for citation in citations],
                    "order_snapshot": order,
                }
                trace_store.add(
                    request.session_id,
                    "workflow_completed",
                    {
                        "session_id": request.session_id,
                        **workflow,
                        "risk_level": "high",
                        "needs_human_approval": True,
                    },
                )
                trace_store.add(
                    request.session_id,
                    "human_approval_required",
                    {
                        "session_id": request.session_id,
                        "workflow_id": workflow["workflow_id"],
                        "pending_action": "require_approval",
                        "risk_level": "high",
                        "needs_human_approval": True,
                    },
                )
                if order is None:
                    answer = "这个订单没有通过当前用户归属校验，退款诉求需要转人工处理，不能跳过人工审批。"
                else:
                    answer = f"{order_no(order)} 可以进入未发货退款申请判断，但资金动作必须等待人工审批。"
                risk_level = "high"
                next_action = "transfer_to_human"
                needs_human_approval = True
            else:
                answer = detail_call.output_summary
                risk_level = "medium"
                next_action = "ask_clarification" if detail_call.error_type == "missing_order_id" else "transfer_to_human"
                needs_human_approval = False
        else:
            answer = "本轮没有触发业务工具，我会按古河商城公司的公开客服口径回答普通咨询。"
            risk_level = "low"
            next_action = "answer_user"
            needs_human_approval = False

        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=answer,
            facts={"tool_calls": tool_calls, "intent": intent},
            citations=citations,
            workflow=workflow,
            risk_level=risk_level,
            next_action=next_action,
            skip_model=intent == "security_request" or cache_hit,
            skip_reason="security_or_cache_boundary",
        )
        answer = model_result.answer

        trace_store.add(
            request.session_id,
            "hook_executed",
            {"session_id": request.session_id, "hook_type": "on_completion", "redacted": True, "degraded": False},
        )
        cost_summary = build_cost_summary(
            request=request,
            intent=intent,
            tool_calls=tool_calls,
            citations=citations,
            workflow=workflow,
            answer=answer,
            cache_hit=cache_hit,
            answer_model_used=model_result.used_model,
        )
        trace_store.add(
            request.session_id,
            "cost_recorded",
            cost_summary,
        )
        trace_store.add(
            request.session_id,
            "final_answer_generated",
            {"session_id": request.session_id, "intent": intent, "status": "success", "risk_level": risk_level},
        )

        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            citations=citations,
            tool_calls=tool_calls,
            reasoning_summary=[
                "Trace 记录的是公开执行摘要：Runtime Context、Context、Tool、RAG、Workflow/HITL、Hooks 和 Cost。",
                "tool_calls 与 citations 是可观察证据，不是 hidden CoT。",
                "系统提示词、hidden reasoning、隐私原文和内部堆栈不会写入公开 trace。",
            ],
            reasoning_content=None,
            session_state={
                "agent_version": "step-40-cost-governance",
                "message_count": message_count,
                "intent": intent,
                "risk_level": risk_level,
                "next_action": next_action,
                "needs_human_approval": needs_human_approval,
                "workflow": workflow,
                "model_answer": model_result.model_dump(),
                "cost_summary": cost_summary,
                "trace": {
                    "schema_version": TRACE_SCHEMA_VERSION,
                    "event_count": len(trace_store.list(request.session_id)),
                    "public_trace_only": True,
                    "hidden_cot_exposed": False,
                },
                "next_gap": "第九幕已经能解释、评测、归因和控成本；下一幕要进入大促总演习。",
            },
        )

    @observe_operation("resume")
    def resume(self, request: ChatResumeRequest) -> ChatResumeResponse:
        """恢复暂停的 HITL workflow，校验令牌、复核业务事实并保持幂等。"""
        checkpoint = WORKFLOW_CHECKPOINTS.get((request.session_id, request.workflow_id))
        if checkpoint is None:
            return self._blocked_resume(request, "checkpoint_not_found")
        if checkpoint["resume_token"] != request.resume_token:
            return self._blocked_resume(request, "invalid_resume_token")
        if request.reviewer_role != "after_sale_manager":
            return self._blocked_resume(request, "invalid_reviewer_role")
        recheck = business_recheck(checkpoint)
        if not recheck["passed"]:
            return self._blocked_resume(request, "business_fact_drift", business_recheck_payload=recheck)
        workflow = dict(checkpoint["workflow"])
        terminal_decision = {"completed": "approved", "rejected": "rejected"}.get(str(workflow.get("status")))
        if terminal_decision and request.decision != terminal_decision:
            return self._blocked_resume(request, "workflow_already_resolved")
        idempotency_key = checkpoint["idempotency_key"]
        idempotent_replay = idempotency_key in SUBMITTED_ACTIONS
        if request.decision == "approved":
            workflow["status"] = "completed"
            workflow["pending_action"] = "approval_accepted"
            answer = "售后主管已批准模拟退款申请，系统记录审批通过；本项目版本不执行真实资金退款。"
            status: Literal["completed", "paused", "rejected", "blocked"] = "completed"
            request_id = SUBMITTED_ACTIONS.setdefault(idempotency_key, {"request_id": f"refund-{request.workflow_id}"})["request_id"]
        elif request.decision == "rejected":
            workflow["status"] = "rejected"
            workflow["pending_action"] = "approval_rejected"
            answer = "售后主管已拒绝本次模拟退款申请，Agent 只能把结果告知用户，不能绕过人工审批。"
            status = "rejected"
            request_id = None
        else:
            workflow["status"] = "paused"
            workflow["pending_action"] = "need_more_info"
            answer = "售后主管要求补充信息，workflow 继续暂停，等待用户或客服补齐材料。"
            status = "paused"
            request_id = None
        checkpoint["workflow"] = workflow
        trace_store.add(request.session_id, "workflow_resumed", {"session_id": request.session_id, "workflow_id": request.workflow_id, "status": status})
        trace_store.add(request.session_id, "human_approval_resolved", {"session_id": request.session_id, "workflow_id": request.workflow_id, "decision": request.decision, "reviewer_role": request.reviewer_role, "status": status})
        cost_summary = {
            "schema_version": "cost_summary_v1",
            "path_type": "hitl_resume_path",
            "model_calls": {"route_planner": 0, "final_answer": 0, "extra_reasoning": 0},
            "tool_call_count": 0,
            "business_tool_call_count": 0,
            "workflow": {"used_langgraph": True, "workflow_id": request.workflow_id, "hitl_required": False, "status": status},
            "safety_boundary": {"cost_control_does_not_skip_hitl": True, "not_finops_or_billing_system": True},
        }
        trace_store.add(request.session_id, "cost_recorded", cost_summary)
        return ChatResumeResponse(
            session_id=request.session_id,
            workflow_id=request.workflow_id,
            status=status,
            answer=answer,
            resume_result={"accepted": True, "decision": request.decision, "idempotent_replay": idempotent_replay, "request_id": request_id, "reason": "approval_recorded"},
            workflow=workflow,
            business_recheck=recheck,
            session_state={
                "agent_version": "step-40-cost-governance",
                "workflow": workflow,
                "cost_summary": cost_summary,
                "trace": {"schema_version": TRACE_SCHEMA_VERSION, "event_count": len(trace_store.list(request.session_id)), "public_trace_only": True, "hidden_cot_exposed": False},
            },
        )

    def _blocked_resume(self, request: ChatResumeRequest, reason: str, business_recheck_payload: dict[str, Any] | None = None) -> ChatResumeResponse:
        """构造被阻断的恢复响应，把失败原因显式暴露给项目观察台。"""
        recheck = business_recheck_payload or {"passed": False, "reason": reason, "mismatches": {}}
        return ChatResumeResponse(
            session_id=request.session_id,
            workflow_id=request.workflow_id,
            status="blocked",
            answer="审批恢复没有通过校验，不能继续执行高风险售后动作。",
            resume_result={"accepted": False, "decision": request.decision, "idempotent_replay": False, "request_id": None, "reason": reason},
            workflow=None,
            business_recheck=recheck,
            session_state={
                "agent_version": "step-40-cost-governance",
                "workflow": None,
                "trace": {"schema_version": TRACE_SCHEMA_VERSION, "event_count": len(trace_store.list(request.session_id)), "public_trace_only": True},
            },
        )
