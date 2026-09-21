"""Pydantic 请求响应模型。API 契约集中放在这里，避免散在 main.py。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "order_query", "member_query", "refund_request", "unknown"]

RiskLevel = Literal["low", "medium", "high"]

NextAction = Literal["answer_user", "ask_clarification", "transfer_to_human"]

ToolStatus = Literal["success", "error"]

SourceType = Literal["user_message", "runtime_context", "session_memory", "tool_observation", "rag_snippet", "workflow_state"]

TrustLevel = Literal["trusted", "verified", "session", "external", "untrusted"]

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
    citation_id: str
    source_title: str
    source_path: str
    policy_id: str
    snippet: str

class ToolAction(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    reason: str

class Observation(BaseModel):
    tool_name: str
    status: ToolStatus
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    omitted_fields: list[str] = Field(default_factory=list)

class ToolCallRecord(BaseModel):
    action: ToolAction
    observation: Observation

class ContextItem(BaseModel):
    item_id: str
    source_type: SourceType
    trust_level: TrustLevel
    content: str
    facts: dict[str, Any] = Field(default_factory=dict)
    allowed_for_model: bool = True
    conflict_group: str | None = None
    decision: str

class ContextBuildReport(BaseModel):
    selected_items: list[ContextItem]
    model_context: list[str]
    conflict_resolutions: list[str]
    excluded_items: list[ContextItem]

class WorkflowState(BaseModel):
    workflow_id: str
    workflow_type: Literal["unshipped_refund", "unknown"]
    status: Literal["running", "paused", "blocked"]
    order_id: str | None
    pending_action: str
    frozen_fields: dict[str, Any] = Field(default_factory=dict)
    boundary: str
    approval_id: str | None = None
    resume_token: str | None = None
    idempotency_key: str | None = None

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    workflow: WorkflowState | None
    context_report: ContextBuildReport
    next_action: NextAction
    risk_level: RiskLevel
    needs_human_approval: bool
    reasoning_summary: list[str]
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
