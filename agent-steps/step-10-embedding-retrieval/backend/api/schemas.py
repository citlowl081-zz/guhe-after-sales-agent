"""Step 10 数据结构。

这一步新增 `VectorRecord`，表示一个知识 chunk 及其 embedding。
"""

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
    """调试后台发给 Agent 的一次聊天请求。"""

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
    """粗意图识别结果，用于调试和 Prompt 说明。"""

    intent: Intent
    matched_keywords: list[str] = Field(default_factory=list)
    explanation: str


class SourceDocument(BaseModel):
    """知识库中的一篇 Markdown 原文。"""

    source_path: str
    title: str
    metadata: dict[str, Any]
    body: str


class KnowledgeSection(BaseModel):
    """按 Markdown 标题解析出来的业务章节。"""

    source_path: str
    document_title: str
    section_index: int
    section: str
    chunk_id: str | None = None
    keywords: list[str] = Field(default_factory=list)
    effective_status: str = "active"
    text: str
    metadata: dict[str, Any]


class KnowledgeChunk(BaseModel):
    """用于 embedding 和向量召回的知识片段。"""

    chunk_id: str
    title: str
    source_path: str
    section: str
    keywords: list[str]
    effective_status: str = "active"
    text: str


class VectorRecord(BaseModel):
    """向量库中的一条记录：知识片段加对应 embedding。"""

    chunk: KnowledgeChunk
    embedding: list[float]


class KnowledgeHit(BaseModel):
    """向量检索命中的知识片段和相似度分数。"""

    chunk: KnowledgeChunk
    score: float = Field(ge=0, le=1)


class CostSummary(BaseModel):
    """一次回答的成本趋势摘要。"""

    prompt_tokens: int
    answer_tokens: int
    total_tokens: int
    estimated_input_cost_cny: float
    estimated_output_cost_cny: float
    estimated_total_cost_cny: float
    context_chars: int
    pricing_note: str


class ChatResponse(BaseModel):
    """Agent 返回给调试后台的完整响应。"""

    session_id: str
    answer: str
    intent: Intent
    intent_result: IntentResult
    cost_summary: CostSummary
    reasoning_summary: list[str]
    session_state: dict[str, Any]
