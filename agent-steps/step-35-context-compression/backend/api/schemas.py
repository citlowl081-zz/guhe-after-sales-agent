"""Pydantic 请求响应模型。项目 API 契约集中放在这里。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "order_query", "refund_request", "unknown"]

RiskLevel = Literal["low", "medium", "high"]

NextAction = Literal["answer_user", "ask_clarification", "transfer_to_human"]

SourceType = Literal["runtime_context", "session_memory", "history_message", "tool_observation", "rag_snippet", "workflow_state"]

class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="当前对话会话 ID")
    runtime_user_id: str = Field(..., description="当前登录用户 ID，由调用方确认")
    runtime_nickname: str | None = Field(default=None, description="当前用户昵称")
    runtime_member_level: str | None = Field(default=None, description="当前会员等级，由调用方确认")
    runtime_risk_level: str | None = Field(default=None, description="当前账号风险等级，由调用方确认")
    user_message: str = Field(..., description="用户输入的问题")
    history_messages: list[HistoryMessage] = Field(default_factory=list, description="调用方传入的历史消息")
    reasoning_view: ReasoningView = "default"
    debug: bool = True
    runtime_context: dict[str, Any] | None = None

class Citation(BaseModel):
    citation_id: str
    source_title: str
    source_path: str
    policy_id: str
    snippet: str

class ContextCandidate(BaseModel):
    item_id: str
    source_type: SourceType
    content: str
    token_estimate: int
    relevance_score: int
    protected: bool = False
    keep_reason: str | None = None

class CompressionReport(BaseModel):
    max_context_tokens: int
    recent_window_size: int
    input_items: list[ContextCandidate]
    kept_items: list[ContextCandidate]
    compressed_summary: str
    dropped_items: list[ContextCandidate]
    token_estimate_before: int
    token_estimate_after: int
    lost_in_middle_guardrails: list[str]

class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    compression_report: CompressionReport
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
