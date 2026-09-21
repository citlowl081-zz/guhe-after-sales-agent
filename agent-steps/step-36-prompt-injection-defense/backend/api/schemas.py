"""Pydantic 请求响应模型。项目 API 契约集中放在这里。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "order_query", "refund_request", "security_request", "unknown"]

RiskLevel = Literal["low", "medium", "high"]

NextAction = Literal["answer_user", "ask_clarification", "transfer_to_human"]

ExternalSourceType = Literal["user", "tool", "rag"]

class ExternalText(BaseModel):
    source_type: ExternalSourceType
    source_id: str
    content: str

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="当前对话会话 ID")
    runtime_user_id: str = Field(..., description="当前登录用户 ID，由调用方确认")
    runtime_nickname: str | None = Field(default=None, description="当前用户昵称")
    runtime_member_level: str | None = Field(default=None, description="当前会员等级，由调用方确认")
    runtime_risk_level: str | None = Field(default=None, description="当前账号风险等级，由调用方确认")
    user_message: str = Field(..., description="用户输入的问题")
    external_texts: list[ExternalText] = Field(default_factory=list, description="工具或 RAG 带回来的外部文本")
    reasoning_view: ReasoningView = "default"
    debug: bool = True
    runtime_context: dict[str, Any] | None = None

class Citation(BaseModel):
    citation_id: str
    source_title: str
    source_path: str
    policy_id: str
    snippet: str

class SafetyScan(BaseModel):
    source_type: ExternalSourceType
    source_id: str
    tainted: bool
    categories: list[str]
    sanitized_content: str
    allowed_for_model: bool
    handling: str

class SafetyDecision(BaseModel):
    blocked_user_request: bool
    refused_topics: list[str]
    source_scans: list[SafetyScan]
    public_summary: list[str]
    redaction_applied: bool

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    safety_decision: SafetyDecision
    sanitized_context: list[str]
    next_action: NextAction
    risk_level: RiskLevel
    needs_human_approval: bool
    reasoning_summary: list[str]
    reasoning_content: str | None = None
    session_state: dict[str, Any]

class ChatResumeRequest(BaseModel):
    session_id: str
    workflow_id: str
    resume_token: str
    decision: Literal["approved", "rejected", "needs_more_info"]
    reviewer_role: str = "after_sale_manager"
    reviewer_note: str | None = None

class ChatResumeResponse(BaseModel):
    session_id: str
    workflow_id: str
    status: Literal["submitted", "rejected", "paused", "blocked"]
    answer: str
    workflow: dict[str, Any] | None = None
    result: dict[str, Any]
    business_recheck: dict[str, Any]
    session_state: dict[str, Any]
