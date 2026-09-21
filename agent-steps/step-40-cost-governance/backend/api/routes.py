"""FastAPI 路由层，只负责接入 Agent、Trace、Eval 或 Feedback。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import observe_operation

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
from config.settings import CASES_PATH, load_agent_capabilities
from agents.customer_service_agent import Step40Agent
from observability.trace import trace_store
from evals.runner import EvalRunner
from feedback.attribution import BACKFILLED_CASES, FEEDBACK_RECORDS, FailureAttributor, build_backfilled_case

app = FastAPI(title="Step 40 Guhe Agent Cost Governance")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = Step40Agent()

eval_runner = EvalRunner(agent, CASES_PATH)
eval_runner.backfilled_cases = BACKFILLED_CASES

failure_attributor = FailureAttributor()

@app.get("/health")
def health() -> dict[str, str]:
    """提供步骤快照健康检查。"""
    return {"status": "ok", "step": "40"}

@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """返回当前步程能力清单。"""
    return load_agent_capabilities()

@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    """处理一次聊天请求。"""
    return agent.chat(request)

@app.post("/chat/resume")
def chat_resume(request: ChatResumeRequest) -> ChatResumeResponse:
    """恢复一个暂停在 HITL 节点的售后 workflow。"""
    return agent.resume(request)

@app.get("/sessions/{session_id}/trace")
def session_trace(session_id: str) -> list[dict[str, Any]]:
    """返回指定会话的公开 Trace 事件。"""
    return [event.model_dump() for event in trace_store.list(session_id)]

@app.post("/eval/run")
def run_eval(request: EvalRunRequest) -> EvalRunResponse:
    """运行项目评测用例。"""
    return eval_runner.run(request.case_id)

@app.post("/feedback/submit")
@observe_operation("feedback")
def submit_feedback(request: FeedbackRequest) -> FeedbackSubmitResponse:
    """提交用户反馈并生成归因和回填用例。"""
    trace_events = trace_store.list(request.session_id)
    eval_report = eval_runner.run(request.case_id) if request.case_id else EvalRunResponse(summary={"schema_version": "eval_report_v1"}, total=0, passed=0, failed=0, results=[])
    eval_result = eval_report.results[0] if eval_report.results else None
    attributions = failure_attributor.attribute(
        feedback=request,
        trace_events=trace_events,
        eval_result=eval_result,
    )
    backfilled_case = build_backfilled_case(request, attributions)
    BACKFILLED_CASES.append(backfilled_case)
    record = FeedbackRecord(
        feedback_id=f"fb-{len(FEEDBACK_RECORDS) + 1}",
        session_id=request.session_id,
        case_id=request.case_id,
        rating=request.rating,
        user_comment=request.user_comment,
        observed_answer=request.observed_answer,
        trace_event_names=[event.event_type for event in trace_events],
        eval_failure_categories=eval_result.failure_categories if eval_result else [],
        attributions=attributions,
        backfilled_case=backfilled_case,
    )
    FEEDBACK_RECORDS.append(record)
    return FeedbackSubmitResponse(record=record, eval_report=eval_report)
