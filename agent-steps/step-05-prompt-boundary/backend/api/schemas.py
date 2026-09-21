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
    """古河商城调用 `/chat` 时传入的请求格式。"""

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
    """沿用 Step 04 的粗意图结果。"""

    intent: Intent
    matched_keywords: list[str] = Field(default_factory=list)
    explanation: str


class PolicyDocument(BaseModel):
    """本步故意全量注入 Prompt 的规则文档。"""

    doc_id: str
    title: str
    # status 是本步观察新旧规则冲突的关键字段，不是完整知识治理流程。
    status: Literal["current", "legacy", "draft"]
    keywords: list[str]
    body: str


class ContextConflict(BaseModel):
    """全量注入后暴露出的规则冲突线索。"""

    # 冲突线索只用于调试观察，不能被当成自动审批或政策裁决结果。
    topic: str
    newer_doc_id: str
    older_doc_id: str
    reason: str


class ChatResponse(BaseModel):
    """合并后的 Step 05 仍只返回当前阶段能解释清楚的字段。"""

    session_id: str
    answer: str
    intent: Intent
    intent_result: IntentResult
    reasoning_summary: list[str]
    session_state: dict[str, Any]
