"""Pydantic 请求响应模型。API 契约集中放在这里，避免散在 main.py。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "order_query", "product_query", "refund_request", "return_request", "unknown"]

RiskLevel = Literal["low", "medium", "high"]

NextAction = Literal["answer_user", "ask_clarification", "transfer_to_human"]

ToolStatus = Literal["success", "error"]

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="当前对话会话 ID")
    runtime_user_id: str = Field(..., description="当前登录用户 ID，由调用方确认")
    runtime_nickname: str | None = Field(default=None, description="当前用户昵称")
    runtime_member_level: str | None = Field(default=None, description="当前会员等级")
    runtime_risk_level: str | None = Field(default=None, description="当前账号风险等级")
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

class HighRiskAssessment(BaseModel):
    action_type: Literal["refund", "return", "unknown"]
    order_id: str | None
    eligibility_status: Literal["eligible_for_application", "not_eligible", "needs_clarification", "blocked"]
    risk_level: RiskLevel
    needs_human_approval: bool
    evidence_checklist: list[str]
    policy_basis: list[Citation]
    reasons: list[str]
    blocked_write_actions: list[str]

class WorkflowSummary(BaseModel):
    workflow_id: str
    workflow_type: Literal["unshipped_refund", "received_return", "unknown"]
    status: Literal["running", "completed", "blocked", "paused", "rejected"]
    current_node: str
    pending_action: str
    node_history: list[str]
    used_langgraph: bool
    boundary: str
    approval_id: str | None = None
    resume_token: str | None = None
    idempotency_key: str | None = None
    frozen_fields: dict[str, Any] = Field(default_factory=dict)

class ApprovalRequest(BaseModel):
    approval_id: str
    workflow_id: str
    status: Literal["pending"]
    required_role: str
    submitted_by: str
    risk_summary: str
    decision_options: list[Literal["approved", "rejected", "needs_more_info"]]
    boundary: str

class ChatResumeRequest(BaseModel):
    session_id: str
    workflow_id: str
    resume_token: str
    reviewer_id: str
    reviewer_role: str
    decision: Literal["approved", "rejected", "needs_more_info"]
    reviewer_note: str | None = None

class ResumeResult(BaseModel):
    decision: Literal["approved", "rejected", "needs_more_info"]
    accepted: bool
    idempotent_replay: bool = False
    request_id: str | None = None
    reason: str

class ChatResumeResponse(BaseModel):
    session_id: str
    workflow_id: str
    status: Literal["completed", "paused", "rejected", "blocked"]
    answer: str
    workflow: WorkflowSummary | None
    approval: ApprovalRequest | None
    resume_result: ResumeResult
    business_recheck: dict[str, Any]
    session_state: dict[str, Any]

class MemoryDecision(BaseModel):
    key: str
    value: Any | None = None
    accepted: bool
    reason: str
    ttl: str | None = None

class SessionMemorySnapshot(BaseModel):
    last_order_id: str | None = None
    last_product_name: str | None = None
    recent_intent: Intent | None = None
    low_risk_preferences: dict[str, str] = Field(default_factory=dict)
    excluded_items: list[str] = Field(default_factory=list)
    ttl_policy: str = "只在当前用户的当前 session 内保留；不跨用户复用，也不写长期用户画像。"

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    after_sale_assessment: HighRiskAssessment | None = None
    workflow: WorkflowSummary | None = None
    approval: ApprovalRequest | None = None
    memory_update: list[MemoryDecision]
    memory_snapshot: SessionMemorySnapshot
    next_action: NextAction
    risk_level: RiskLevel
    needs_human_approval: bool
    reasoning_summary: list[str]
    session_state: dict[str, Any]
