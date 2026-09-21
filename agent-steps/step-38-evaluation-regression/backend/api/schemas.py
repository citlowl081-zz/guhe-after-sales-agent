"""Pydantic 请求响应模型。项目 API 契约集中放在这里。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "order_query", "refund_request", "security_request", "unknown"]

RiskLevel = Literal["low", "medium", "high"]

NextAction = Literal["answer_user", "ask_clarification", "transfer_to_human"]

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="当前对话会话 ID")
    runtime_user_id: str = Field(..., description="当前登录用户 ID，由调用方确认")
    runtime_nickname: str | None = Field(default=None, description="当前用户昵称")
    runtime_member_level: str | None = Field(default=None, description="当前会员等级，由调用方确认")
    runtime_risk_level: str | None = Field(default=None, description="当前账号风险等级，由调用方确认")
    user_message: str = Field(..., description="用户输入的问题")
    reasoning_view: ReasoningView = "default"
    debug: bool = True
    runtime_context: dict[str, Any] | None = None

class Citation(BaseModel):
    source: str
    title: str
    snippet: str
    score: float
    retrieval_stage: Literal["pre_retrieval", "tool_retrieval"] | None = None
    metadata: dict[str, Any] | None = None

class ToolCallTrace(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    output_summary: str
    status: Literal["success", "error"]
    tool_source: Literal["function", "mcp", "preload"] | None = "function"
    risk_level: RiskLevel | None = None
    needs_human_approval: bool | None = None
    next_action: str | None = None
    error_type: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    clarification_field: str | None = None
    clarification_prompt: str | None = None

class TraceEvent(BaseModel):
    event_type: str
    timestamp: datetime
    agent_mode: str | None = None
    step: int | None = None
    schema_version: str
    category: str
    stage: str
    name: str
    status: str
    target: dict[str, Any]
    ids: dict[str, Any]
    summary: dict[str, Any]
    signals: list[str]
    safety: dict[str, Any]
    payload: dict[str, Any]

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[Citation]
    tool_calls: list[ToolCallTrace]
    reasoning_summary: list[str]
    reasoning_content: str | None = None
    session_state: dict[str, Any]

class ChatResumeRequest(BaseModel):
    session_id: str = Field(..., description="要恢复的会话 ID")
    workflow_id: str = Field(..., description="待恢复 workflow ID")
    resume_token: str = Field(..., description="暂停时返回的恢复令牌")
    reviewer_id: str = Field(..., description="审批人 ID")
    reviewer_role: str = Field(..., description="审批人角色")
    decision: Literal["approved", "rejected", "needs_more_info"]
    reviewer_note: str | None = None

class ChatResumeResponse(BaseModel):
    session_id: str
    workflow_id: str
    status: Literal["completed", "paused", "rejected", "blocked"]
    answer: str
    resume_result: dict[str, Any]
    workflow: dict[str, Any] | None
    business_recheck: dict[str, Any]
    session_state: dict[str, Any]

class EvalRunRequest(BaseModel):
    case_id: str | None = None

class EvalCaseResult(BaseModel):
    case_id: str
    passed: bool
    user_message: str
    expected_signals: list[str]
    actual_answer: str
    actual_tools: list[str]
    missing_signals: list[str]
    actual_citations: list[str] = []
    actual_trace_events: list[str] = []
    missing_tools: list[str] = []
    unexpected_tools: list[str] = []
    missing_citations: list[str] = []
    forbidden_citation_hits: list[str] = []
    missing_trace_events: list[str] = []
    missing_session_state: list[str] = []
    forbidden_text_hits: list[str] = []
    failure_categories: list[str] = []

class EvalRunResponse(BaseModel):
    total: int
    passed: int
    failed: int
    summary: dict[str, Any]
    results: list[EvalCaseResult]
