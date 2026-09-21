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
from context.compression import *
from workflows.resume import create_workflow_checkpoint, handle_resume_request

class Step35Agent:
    """Step 35：上下文压缩与 Sliding Window。"""

    def __init__(self) -> None:
        """初始化本步服务对象，把可替换依赖固定在实例上，便于测试和项目演进。"""
        self.compressor = ContextCompressor()

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """编排本步 Agent 主链路，串联意图、上下文、工具、Trace、Eval 或成本治理能力。"""
        load_guhe_env()
        MESSAGE_COUNT_BY_SESSION[request.session_id] = MESSAGE_COUNT_BY_SESSION.get(request.session_id, 0) + 1
        message_count = MESSAGE_COUNT_BY_SESSION[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别压缩上下文的当前目标", observe=True, intent=intent)
        memory = current_memory(request.session_id, request.runtime_user_id)
        explicit_order_id = extract_order_ids(request.user_message)[0] if extract_order_ids(request.user_message) else None
        page_order_id = (request.runtime_context or {}).get("current_order_id") or (request.runtime_context or {}).get("relatedOrderNo")
        order_id = explicit_order_id or page_order_id or memory.get("last_order_id")

        candidates: list[ContextCandidate] = [
            ContextCandidate(
                item_id="runtime-context",
                source_type="runtime_context",
                content=runtime_context_summary(request),
                token_estimate=estimate_tokens(runtime_context_summary(request)),
                relevance_score=100,
                protected=True,
                keep_reason="可信系统上下文不能被窗口裁掉。",
            )
        ]
        if memory.get("last_order_id"):
            content = f"Session Memory：最近订单 {memory['last_order_id']}，最近意图 {memory.get('recent_intent')}。"
            candidates.append(
                ContextCandidate(
                    item_id="session-memory",
                    source_type="session_memory",
                    content=content,
                    token_estimate=estimate_tokens(content),
                    relevance_score=88 if order_id == memory.get("last_order_id") else 55,
                    protected=False,
                    keep_reason="当前追问依赖最近订单。" if order_id == memory.get("last_order_id") else None,
                )
            )
        candidates.extend(build_history_candidates(request.history_messages, request.user_message))

        order_candidate, order = build_order_context(str(order_id), request.runtime_user_id, request.runtime_context) if order_id else (None, None)
        if order_candidate:
            candidates.append(order_candidate)
        rag_candidate = build_rag_context(intent)
        if rag_candidate:
            candidates.append(rag_candidate)
        workflow_candidate = build_workflow_context(intent, str(order_id) if order_id else None)
        if workflow_candidate:
            candidates.append(workflow_candidate)
        workflow = create_workflow_checkpoint(
            request,
            order,
            boundary="上下文压缩必须保护 workflow state，但恢复动作仍只能走 /chat/resume。",
        ) if intent == "refund_request" and order is not None else None

        report = self.compressor.compress(candidates, current_message=request.user_message)
        log_guhe_event("CONTEXT_COMPRESSED", "上下文已按保护项、相关性和最近窗口压缩", observe=True, input_count=len(report.input_items), kept_count=len(report.kept_items), dropped_count=len(report.dropped_items), tokens_before=report.token_estimate_before, tokens_after=report.token_estimate_after)
        if order is not None:
            memory["last_order_id"] = order_no(order)
        memory["recent_intent"] = intent
        citations = [POLICIES["POLICY-REFUND-UNSHIPPED"]] if intent == "refund_request" else []
        answer, next_action, risk_level, needs_human_approval = self._build_answer(intent=intent, order=order, order_id=order_id, report=report)
        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=answer,
            facts={"compression_report": report, "order": order},
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
            compression_report=report,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=needs_human_approval,
            reasoning_summary=[
                "Sliding Window 保留最近几轮，不让最新问题被旧历史挤出去。",
                "Context Relevance 会把当前订单、当前意图相关的中间历史捞回来。",
                "旧闲聊进入压缩摘要，不能继续占满模型上下文。",
            ],
            session_state={
                "agent_version": "step-35-context-compression",
                "message_count": message_count,
                "model_answer": model_result.model_dump(),
                "memory": memory,
                "workflow": workflow,
                "compression": {
                    "before": report.token_estimate_before,
                    "after": report.token_estimate_after,
                    "kept_count": len(report.kept_items),
                    "dropped_count": len(report.dropped_items),
                },
                "next_gap": "窗口和压缩能控制长度，但外部文本里带脏指令时还要做注入防护。",
            },
        )

    @observe_operation("resume")
    def resume(self, request: ChatResumeRequest) -> ChatResumeResponse:
        """恢复上一轮暂停的高风险售后 workflow。"""
        return handle_resume_request(request, agent_version="step-35-context-compression")

    @staticmethod
    def _build_answer(
        *,
        intent: Intent,
        order: dict[str, Any] | None,
        order_id: str | None,
        report: CompressionReport,
    ) -> tuple[str, NextAction, RiskLevel, bool]:
        """根据结构化事实生成项目可读回答，避免把复杂分支散落在路由层。"""
        if intent == "refund_request":
            if order is None:
                return ("压缩后仍然没有可信订单事实，退款问题要先补订单信息。", "ask_clarification", "high", True)
            return (
                f"我保留了订单 {order_no(order)}、退款政策和 workflow 状态；退款仍然需要人工审批，旧聊天不会挤掉这个边界。",
                "transfer_to_human",
                "high",
                True,
            )
        if order is not None:
            return (
                f"压缩后保留了当前订单 {order_no(order)} 的工具事实，物流状态是 {logistics_status_from_order(order)}。",
                "answer_user",
                "low",
                False,
            )
        return (f"本轮上下文已从 {report.token_estimate_before} 个估算 token 压到 {report.token_estimate_after} 个估算 token。", "answer_user", "low", False)
