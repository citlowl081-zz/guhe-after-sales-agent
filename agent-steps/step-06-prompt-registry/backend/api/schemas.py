"""Pydantic 请求响应模型。早期项目从这里建立稳定 API 契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ReasoningView = Literal["default", "off", "summary", "observe"]
Intent = Literal[
    "general_chat",
    "promotion_consult",
    "product_consult",
    "order_query",
    "refund_request",
    "complaint",
    "unknown",
]


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="当前对话会话 ID")
    runtime_user_id: str = Field(..., description="调试后台当前选择的可信用户 ID")
    runtime_nickname: str | None = Field(default=None, description="调试后台当前选择的用户昵称")
    runtime_member_level: str | None = Field(default=None, description="调试后台当前选择的会员等级")
    runtime_risk_level: str | None = Field(default=None, description="调试后台当前选择的风险等级")
    user_message: str = Field(..., description="用户输入的问题")
    reasoning_view: ReasoningView = "default"
    debug: bool = True
    runtime_context: dict[str, Any] | None = None


class IntentResult(BaseModel):
    intent: Intent
    matched_keywords: list[str] = Field(default_factory=list)
    explanation: str


class PromptFragment(BaseModel):
    """Prompt Registry 里的一个片段。"""

    fragment_id: str
    title: str
    # priority 让规则顺序显式化，避免谁写在前面全靠拼字符串时的手感。
    priority: int
    # enabled=False 的片段可以留档但不进入 Prompt，这是旧活动复盘治理的第一步。
    enabled: bool = True
    applies_to: list[str]
    tags: list[str] = Field(default_factory=list)
    content: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: Intent
    intent_result: IntentResult
    reasoning_summary: list[str]
    session_state: dict[str, Any]
