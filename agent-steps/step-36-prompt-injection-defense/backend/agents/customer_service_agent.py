"""客服 Agent 编排层，连接本步新增能力和公开响应。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat, observe_operation

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
from config.settings import load_agent_capabilities, load_guhe_env
from models.answer_client import compose_grounded_answer
from state.session_state import *
from rag.knowledge import *
from tools.runtime_context import *
from tools.planning import *
from integrations.ecommerce_client import *
from safety.prompt_guard import *
from workflows.resume import create_workflow_checkpoint, handle_resume_request

class Step36Agent:
    """Step 36：Prompt Injection 防护和公开安全摘要。"""

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """编排本步 Agent 主链路，串联意图、上下文、工具、Trace、Eval 或成本治理能力。"""
        load_guhe_env()
        MESSAGE_COUNT_BY_SESSION[request.session_id] = MESSAGE_COUNT_BY_SESSION.get(request.session_id, 0) + 1
        message_count = MESSAGE_COUNT_BY_SESSION[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别本轮业务与安全意图", observe=True, intent=intent)
        external_texts = request.external_texts or default_external_texts(intent)
        safety = build_safety_decision(request.user_message, external_texts)
        log_guhe_event("SOURCE_SAFETY_SCANNED", "用户输入与外部文本已完成 Prompt Injection 检查", observe=True, blocked_user_request=safety.blocked_user_request, tainted_source_count=sum(1 for scan in safety.source_scans if scan.tainted), refused_topics=safety.refused_topics)
        sanitized_context = build_sanitized_context(safety)
        log_guhe_event("CONTEXT_SANITIZED", "污染指令已隔离，模型上下文已重建", observe=True, context_item_count=len(sanitized_context), redaction_applied=safety.redaction_applied)
        citations = [SAFE_REFUND_POLICY] if intent == "refund_request" else []
        order = self._load_order_from_context(request) if intent == "refund_request" else None
        workflow = create_workflow_checkpoint(
            request,
            order,
            boundary="Prompt Injection 防护会隔离外部指令，但不能绕过 HITL 恢复通道。",
        ) if order is not None else None
        answer, next_action, risk_level, needs_human_approval = self._build_answer(
            request=request,
            intent=intent,
            safety=safety,
            sanitized_context=sanitized_context,
        )
        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=answer,
            facts={"safety": safety, "sanitized_context": sanitized_context},
            citations=citations,
            workflow=workflow,
            risk_level=risk_level,
            next_action=next_action,
            skip_model=safety.blocked_user_request,
            skip_reason="security_boundary",
        )
        answer = model_result.answer
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            safety_decision=safety,
            sanitized_context=sanitized_context,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=needs_human_approval,
            reasoning_summary=[
                "系统提示词、工具细节、hidden reasoning 和内部策略不能对外泄露。",
                "用户、工具和 RAG 都按外部文本处理；命中污染后只保留脱敏后的公开安全摘要。",
                "外部文本里的指令不能覆盖系统规则、权限校验或高风险审批边界。",
            ],
            reasoning_content=build_observe_reasoning_content(request, safety, external_texts),
            session_state={
                "agent_version": "step-36-prompt-injection-defense",
                "message_count": message_count,
                "model_answer": model_result.model_dump(),
                "safety": {
                    "blocked_user_request": safety.blocked_user_request,
                    "refused_topics": safety.refused_topics,
                    "tainted_sources": [
                        {"source_type": scan.source_type, "source_id": scan.source_id, "categories": scan.categories}
                        for scan in safety.source_scans
                        if scan.tainted
                    ],
                    "redaction_applied": safety.redaction_applied,
                },
                "workflow": workflow,
                "next_gap": "Agent 已经知道什么可信、什么该记、什么不能泄露；下一幕要拿出可复盘证据解释它为什么这么回答。",
            },
        )

    @observe_operation("resume")
    def resume(self, request: ChatResumeRequest) -> ChatResumeResponse:
        """恢复上一轮暂停的高风险售后 workflow。"""
        return handle_resume_request(request, agent_version="step-36-prompt-injection-defense")

    @staticmethod
    def _build_answer(
        *,
        request: ChatRequest,
        intent: Intent,
        safety: SafetyDecision,
        sanitized_context: list[str],
    ) -> tuple[str, NextAction, RiskLevel, bool]:
        """根据结构化事实生成项目可读回答，避免把复杂分支散落在路由层。"""
        if safety.blocked_user_request:
            return (
                "我不能提供系统提示词、hidden reasoning、工具细节或内部策略。可以给你公开安全摘要：本轮请求已被识别为受保护信息请求。",
                "answer_user",
                "high",
                False,
            )
        if intent == "refund_request":
            order_id = extract_order_id(request.user_message)
            base = order_answer(order_id, request.runtime_user_id, request.runtime_context)
            tainted_note = " 外部文本里出现的指令已隔离，不能要求我跳过审批或直接退款。" if any(scan.tainted for scan in safety.source_scans) else ""
            return (f"{base} 退款仍需进入人工审批。{tainted_note}", "transfer_to_human", "high", True)
        if intent == "order_query":
            order_id = extract_order_id(request.user_message)
            return (order_answer(order_id, request.runtime_user_id, request.runtime_context), "answer_user", "low", False)
        if any(scan.tainted for scan in safety.source_scans):
            return ("我已经隔离外部文本里的脏指令，只会根据脱敏后的安全摘要继续回答。", "answer_user", "low", False)
        return (f"本轮安全检查通过，进入模型的上下文共有 {len(sanitized_context)} 条安全片段。", "answer_user", "low", False)

    @staticmethod
    def _load_order_from_context(request: ChatRequest) -> dict[str, Any] | None:
        """为恢复 checkpoint 提取当前可信订单事实，不从外部文本里接受审批指令。"""
        order_id = extract_order_id(request.user_message)
        if not order_id:
            return None
        order = find_context_order(request.runtime_context, order_id) or order_fact_from_ecommerce(order_id, request.runtime_user_id)
        if order is None:
            return None
        owner = order_user_id(order)
        if owner and owner != request.runtime_user_id:
            return None
        return order
