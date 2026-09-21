"""Pydantic 请求响应模型。API 契约集中放在这里，避免散在 main.py。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "order_query", "member_query", "refund_request", "unknown"]

RiskLevel = Literal["low", "medium", "high"]

NextAction = Literal["answer_user", "ask_clarification", "transfer_to_human"]

ToolStatus = Literal["success", "error"]

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

class RuntimeContextView(BaseModel):
    trusted_for_model: dict[str, Any]
    system_only: dict[str, Any]
    conflict_notes: list[str]
    permission_decision: dict[str, Any]

class MemoryDecision(BaseModel):
    key: str
    value: Any | None = None
    accepted: bool
    reason: str
    ttl: Literal["session"] | None = None

class SessionMemorySnapshot(BaseModel):
    last_order_id: str | None = None
    recent_intent: Intent | None = None

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: Intent
    tool_calls: list[ToolCallRecord]
    runtime_context_view: RuntimeContextView
    memory_update: list[MemoryDecision] = Field(default_factory=list)
    memory_snapshot: SessionMemorySnapshot | None = None
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
