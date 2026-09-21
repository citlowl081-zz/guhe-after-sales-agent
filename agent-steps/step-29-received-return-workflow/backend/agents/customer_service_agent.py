"""客服 Agent 编排层。这里连接工具、工作流、记忆或上下文模块。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

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
from workflows.after_sale_workflow import WORKFLOW
from policies.after_sale_policy import *

class Step29Agent:
    """Step 29：签收后退货要看时间、商品、原因和政策。"""

    def __init__(self) -> None:
        """初始化本模块对象需要的协作依赖，保持入口层只负责编排。"""
        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理一次 /chat 请求，并返回项目约定的公开响应结构。"""
        load_guhe_env()
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别退款或签收后退货意图", observe=True, intent=intent)
        workflow_state = WORKFLOW.run(request)
        log_guhe_event("WORKFLOW_COMPLETED", "售后流程已停在提交前边界", observe=True, workflow_type=workflow_state["workflow_type"], eligibility=workflow_state["assessment"].eligibility_status)
        assessment = workflow_state["assessment"]
        assert assessment is not None
        workflow = self._workflow_summary(workflow_state)
        next_action: NextAction = "ask_clarification" if assessment.eligibility_status == "needs_clarification" else "transfer_to_human"
        answer = workflow_state["answer"]
        return self._response(
            request=request,
            message_count=message_count,
            intent=intent,
            answer=answer,
            tool_calls=workflow_state["tool_calls"],
            citations=workflow_state["citations"],
            assessment=assessment,
            workflow=workflow,
            next_action=next_action,
        )

    @staticmethod
    def _workflow_summary(state: AfterSaleWorkflowState) -> WorkflowSummary:
        """把内部工作流状态压缩成前端可观察摘要。"""
        return WorkflowSummary(
            workflow_id=state["workflow_id"],
            workflow_type=state["workflow_type"],
            status=state["status"],
            current_node=state["current_node"],
            pending_action=state["pending_action"],
            node_history=state["node_history"],
            used_langgraph=True,
            boundary="退款和退货工作流只判断是否可准备申请；本步不提交申请、不审批、不返回 resume_token。",
        )

    def _response(
        self,
        *,
        request: ChatRequest,
        message_count: int,
        intent: Intent,
        answer: str,
        tool_calls: list[ToolCallRecord],
        citations: list[Citation],
        assessment: HighRiskAssessment,
        workflow: WorkflowSummary,
        next_action: NextAction,
    ) -> ChatResponse:
        """把内部判断结果组装成项目 API 响应。"""
        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=answer,
            facts={"assessment": assessment, "tool_calls": tool_calls},
            citations=citations,
            workflow=workflow if "workflow" in locals() else None,
            risk_level="high" if intent in {"refund_request", "return_request"} else "low",
            next_action=next_action,
        )
        answer = model_result.answer

        session_state = {
            "agent_version": "step-29-received-return-workflow",
            "message_count": message_count,
            "model_answer": model_result.model_dump(),
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "after_sale": {
                "assessment": assessment.model_dump(),
                "write_actions_blocked": True,
            },
            "workflow": workflow.model_dump(),
            "next_gap": "退款和退货申请资格都能判断了；下一步要把 AI 只能提交申请、不能批准这条人工边界立住。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            tool_calls=tool_calls,
            after_sale_assessment=assessment,
            workflow=workflow,
            next_action=next_action,
            risk_level="high" if intent in {"refund_request", "return_request"} else "low",
            needs_human_approval=True if intent in {"refund_request", "return_request"} else False,
            reasoning_summary=[
                "签收后退货进入 LangGraph StateGraph，而不是让模型一句话决定能不能退。",
                "资格判断同时查看签收时间、商品可退属性、退货原因和政策依据。",
                "本步还没有 HITL、/chat/resume、Checkpoint 或幂等提交。",
            ],
            session_state=session_state,
        )
