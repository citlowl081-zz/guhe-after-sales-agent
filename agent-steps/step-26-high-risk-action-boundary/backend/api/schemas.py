"""Step 26：请求、响应和状态模型。类型集中后，路由、工具、工作流都围绕同一套契约协作。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "refund_request", "return_request", "unknown"]

RiskLevel = Literal["low", "medium", "high"]

NextAction = Literal["answer_user", "ask_clarification", "transfer_to_human"]

ToolStatus = Literal["success", "error"]

class ChatRequest(BaseModel):
    """ChatRequest 承载本步逐步长出的 Agent 模块能力。"""
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
    """Citation 承载本步逐步长出的 Agent 模块能力。"""
    citation_id: str
    source_title: str
    source_path: str
    policy_id: str
    snippet: str

class ToolAction(BaseModel):
    """ToolAction 承载本步逐步长出的 Agent 模块能力。"""
    tool_name: str
    arguments: dict[str, Any]
    reason: str

class Observation(BaseModel):
    """Observation 承载本步逐步长出的 Agent 模块能力。"""
    tool_name: str
    status: ToolStatus
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    omitted_fields: list[str] = Field(default_factory=list)

class ToolCallRecord(BaseModel):
    """ToolCallRecord 承载本步逐步长出的 Agent 模块能力。"""
    action: ToolAction
    observation: Observation

class HighRiskAssessment(BaseModel):
    """HighRiskAssessment 承载本步逐步长出的 Agent 模块能力。"""
    action_type: Literal["refund", "return", "unknown"]
    order_id: str | None
    eligibility_status: Literal["eligible_for_application", "not_eligible", "needs_clarification", "blocked"]
    risk_level: RiskLevel
    needs_human_approval: bool
    evidence_checklist: list[str]
    policy_basis: list[Citation]
    reasons: list[str]
    blocked_write_actions: list[str]

class ChatResponse(BaseModel):
    """ChatResponse 承载本步逐步长出的 Agent 模块能力。"""
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    after_sale_assessment: HighRiskAssessment | None = None
    next_action: NextAction
    risk_level: RiskLevel
    needs_human_approval: bool
    reasoning_summary: list[str]
    session_state: dict[str, Any]
