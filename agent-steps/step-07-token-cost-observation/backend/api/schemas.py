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
    fragment_id: str
    title: str
    priority: int
    enabled: bool = True
    applies_to: list[str]
    tags: list[str] = Field(default_factory=list)
    content: str


class CostSummary(BaseModel):
    """Step 07 新增的 token 观察结果。"""

    prompt_tokens: int
    answer_tokens: int
    total_tokens: int
    # token_source 让你知道本轮成本来自平台 usage，还是本地估算兜底。
    token_source: Literal["model_usage", "local_estimate"]
    usage_details: dict[str, Any] = Field(default_factory=dict)
    estimated_input_cost_cny: float
    estimated_output_cost_cny: float
    estimated_total_cost_cny: float
    context_chars: int
    pricing_note: str


class TokenUsage(BaseModel):
    """模型平台返回的 usage 标准化结果。"""

    prompt_tokens: int
    answer_tokens: int
    total_tokens: int
    details: dict[str, Any] = Field(default_factory=dict)


class ChatModelResult(BaseModel):
    # 模型客户端从 Step 07 开始同时返回 answer 和 usage，方便成本模块独立观察。
    answer: str
    usage: TokenUsage | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: Intent
    intent_result: IntentResult
    cost_summary: CostSummary
    reasoning_summary: list[str]
    session_state: dict[str, Any]
