"""Step 22：请求、响应与项目状态模型。把类型集中放在这里，是从单文件脚本迈向接口契约的第一步。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "product_consult", "refund_request", "unknown"]

ToolStatus = Literal["success", "error", "skipped"]

NextAction = Literal["answer_user", "fallback_answer", "transfer_to_human"]

RiskLevel = Literal["low", "medium", "high"]

KnowledgeTopic = Literal["product", "promotion", "after_sale", "shipping"]

class ChatRequest(BaseModel):
    """ChatRequest 项目对象，承载本步逐步长出的 Agent 能力。"""
    session_id: str = Field(..., description="当前对话会话 ID")
    runtime_user_id: str = Field(..., description="调试后台当前选择的可信用户 ID")
    runtime_nickname: str | None = Field(default=None, description="调试后台当前选择的用户昵称")
    runtime_member_level: str | None = Field(default=None, description="调试后台当前选择的会员等级")
    runtime_risk_level: str | None = Field(default=None, description="调试后台当前选择的风险等级")
    user_message: str = Field(..., description="用户输入的问题")
    reasoning_view: ReasoningView = "default"
    debug: bool = True
    runtime_context: dict[str, Any] | None = None

class KnowledgeChunk(BaseModel):
    """KnowledgeChunk 项目对象，承载本步逐步长出的 Agent 能力。"""
    chunk_id: str
    title: str
    source_path: str
    section: str
    topic: KnowledgeTopic
    keywords: list[str]
    text: str

class KnowledgeHit(BaseModel):
    """KnowledgeHit 项目对象，承载本步逐步长出的 Agent 能力。"""
    chunk: KnowledgeChunk
    score: float
    matched_keywords: list[str]

class Citation(BaseModel):
    """Citation 项目对象，承载本步逐步长出的 Agent 能力。"""
    citation_id: str
    source_title: str
    source_path: str
    chunk_id: str
    score: float
    snippet: str

class ToolAction(BaseModel):
    """ToolAction 项目对象，承载本步逐步长出的 Agent 能力。"""
    tool_name: str
    arguments: dict[str, Any]
    reason: str

class Observation(BaseModel):
    """Observation 项目对象，承载本步逐步长出的 Agent 能力。"""
    tool_name: str
    status: ToolStatus
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    next_action: NextAction

class ToolCallRecord(BaseModel):
    """ToolCallRecord 项目对象，承载本步逐步长出的 Agent 能力。"""
    action: ToolAction
    observation: Observation
    attempts: int = 1

class ChatResponse(BaseModel):
    """ChatResponse 项目对象，承载本步逐步长出的 Agent 能力。"""
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    next_action: NextAction
    risk_level: RiskLevel
    needs_human_approval: bool
    degraded: bool
    reasoning_summary: list[str]
    session_state: dict[str, Any]
