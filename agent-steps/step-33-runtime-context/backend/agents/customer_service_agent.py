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
from memory.session_memory import SessionMemoryStore
from models.answer_client import compose_grounded_answer
from tools.runtime_context import *
from tools.planning import *
from tools.tool_runtime import *
from state.checkpoints import *
from context.runtime_context import *
from workflows.resume import create_workflow_checkpoint, handle_resume_request

class Step33Agent:
    """Step 33：Runtime Context 是系统可信上下文，不等于用户自述。"""

    def __init__(self) -> None:
        """保留 Step 32 的短期订单记忆，但身份和权限仍交给 Runtime Context。"""
        self.memory_store = SessionMemoryStore()

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理一次 /chat 请求，并返回项目约定的公开响应结构。"""
        load_guhe_env()
        MESSAGE_COUNT_BY_SESSION[request.session_id] = MESSAGE_COUNT_BY_SESSION.get(request.session_id, 0) + 1
        message_count = MESSAGE_COUNT_BY_SESSION[request.session_id]
        memory = self.memory_store.get(request.session_id, request.runtime_user_id)
        intent = classify_intent(request.user_message)
        runtime_context = build_runtime_context(request)
        log_guhe_event("RUNTIME_CONTEXT_BUILT", "已从系统调用方构建可信 Runtime Context", observe=True, intent=intent, runtime_user_id=runtime_context.system_only.get("user_id"), risk_level=runtime_context.system_only.get("risk_level"))
        tool_calls: list[ToolCallRecord] = []
        order: dict[str, Any] | None = None

        order_id = resolve_order_from_runtime_context(request, runtime_context)
        memory_used = False
        if order_id is None and any(term in request.user_message for term in ["刚才那个", "刚刚那个", "上一个订单"]):
            order_id = memory.last_order_id
            memory_used = order_id is not None
        if order_id:
            order, order_call, permission_decision = load_order_for_runtime_context(order_id, runtime_context)
            tool_calls.append(order_call)
            runtime_context.permission_decision = permission_decision
            log_guhe_event("PERMISSION_CHECKED", "订单读取已按 Runtime Context 完成归属校验", observe=True, order_id=order_id, permission_decision=permission_decision)
        memory_update = self.memory_store.update(memory=memory, intent=intent, owned_order=order)

        answer, next_action, risk_level, needs_human_approval = self._build_answer(
            request=request,
            intent=intent,
            runtime_context=runtime_context,
            order=order,
            order_id=order_id,
            memory_used=memory_used,
        )
        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=answer,
            facts={"runtime_context": runtime_context, "order": order, "order_id": order_id, "memory": memory, "memory_used": memory_used},
            risk_level=risk_level,
            next_action=next_action,
        )
        answer = model_result.answer
        workflow = create_workflow_checkpoint(
            request,
            order,
            boundary="Runtime Context 负责确认身份和权限，但高风险退款仍要暂停到 HITL 恢复通道。",
        ) if intent == "refund_request" and order is not None else None
        if workflow:
            log_guhe_event("WORKFLOW_PAUSED", "高风险退款仍停在 HITL 恢复边界", observe=True, workflow_id=workflow["workflow_id"])
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            tool_calls=tool_calls,
            runtime_context_view=runtime_context,
            memory_update=memory_update,
            memory_snapshot=memory,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=needs_human_approval,
            reasoning_summary=[
                "Runtime Context 来自系统调用方，用户在文本里的自称不能覆盖它。",
                "给模型看的上下文只保留昵称、会员等级和页面线索；权限、user_id、风险等级留在系统侧校验。",
                "订单读取先按 Runtime Context 的 user_id 做归属校验，再决定能不能回答。",
            ],
            session_state={
                "agent_version": "step-33-runtime-context",
                "message_count": message_count,
                "model_answer": model_result.model_dump(),
                "runtime_context": runtime_context.model_dump(),
                "memory": memory.model_dump(),
                "memory_policy": {
                    "scope": "session",
                    "long_term_profile": False,
                    "runtime_context_required_for_order_access": True,
                },
                "workflow": workflow,
                "next_gap": "可信 Runtime Context 解决了身份问题，但历史消息、工具结果和 RAG 片段还需要统一编排。",
            },
        )

    @observe_operation("resume")
    def resume(self, request: ChatResumeRequest) -> ChatResumeResponse:
        """恢复上一轮暂停的高风险售后 workflow。"""
        return handle_resume_request(request, agent_version="step-33-runtime-context")

    @staticmethod
    def _build_answer(
        *,
        request: ChatRequest,
        intent: Intent,
        runtime_context: RuntimeContextView,
        order: dict[str, Any] | None,
        order_id: str | None,
        memory_used: bool,
    ) -> tuple[str, NextAction, RiskLevel, bool]:
        """根据事实、上下文和风险边界生成用户可见回答。"""
        member_level = str(runtime_context.trusted_for_model["member_level"])
        account_risk = str(runtime_context.system_only["risk_level"])
        if intent == "refund_request":
            if account_risk == "high":
                return (
                    "当前账号风险等级较高，退款、退货类请求必须转人工复核，不能只靠聊天继续推进。",
                    "transfer_to_human",
                    "high",
                    True,
                )
            return ("退款、退货仍然要走高风险流程，Runtime Context 只负责确认身份和权限。", "transfer_to_human", "high", True)
        if order_id and order is None:
            return ("这个订单没有通过当前登录用户的权限校验，我不能把它当成你的订单回答。", "transfer_to_human", "high", True)
        if order is not None:
            prefix = "根据这段会话刚记住的最近订单，" if memory_used else ""
            return (
                f"{prefix}系统确认当前登录用户可以读取 {order_no(order)}，订单状态 {order_status(order)}，物流状态 {logistics_status_from_order(order)}。",
                "answer_user",
                "low",
                False,
            )
        if order_id is None and any(term in request.user_message for term in ["刚才那个", "刚刚那个", "上一个订单"]):
            return ("我还没有在这段会话里确认过最近订单，请先告诉我订单号或打开对应订单页。", "ask_clarification", "low", False)
        if intent == "member_query":
            if user_claims_vip(request.user_message) and member_level != "vip":
                if member_level == "unknown":
                    return (
                        "系统可信上下文暂未提供可确认的会员等级，我不能按用户自述把你当作 VIP 处理。",
                        "answer_user",
                        "low",
                        False,
                    )
                return (
                    f"系统登录态显示你当前是 {member_level} 会员，我不能按用户自述把你当作 VIP 处理。",
                    "answer_user",
                    "low",
                    False,
                )
            if member_level == "vip":
                return ("系统登录态确认你是 VIP 会员，可以进入古河商城公司的 VIP 服务口径。", "answer_user", "low", False)
            if member_level == "unknown":
                return ("系统可信上下文暂未提供可确认的会员等级，我会先按普通权益咨询处理，不扩大用户自述权限。", "answer_user", "low", False)
            return (f"系统登录态显示你当前是 {member_level} 会员，我会按这个会员等级回答。", "answer_user", "low", False)
        return ("我会按系统传入的登录态、会员等级、风险等级和页面上下文来判断这轮请求。", "answer_user", "low", False)
