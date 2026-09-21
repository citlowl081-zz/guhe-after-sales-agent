"""Step 12 数据结构。

这一步保留 citations，并新增 RAG 质量检查结果。
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


class KnowledgeHit(BaseModel):
    """向量检索命中的知识片段和相似度分数。"""

    chunk: KnowledgeChunk
    score: float = Field(ge=0, le=1)


class VectorRecord(BaseModel):
    """进程内向量库保存的一条知识向量记录。"""

    chunk: KnowledgeChunk
    embedding: list[float]


class Citation(BaseModel):
    """调试后台、评测和排错使用的引用证据。"""

    citation_id: str
    source_title: str
    source_path: str
    chunk_id: str
    score: float
    snippet: str


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


class RagQualityCase(BaseModel):
    """固定问题集里的一条 RAG 质量用例。"""

    case_id: str
    question: str
    expected_chunk_ids: list[str]
    must_fallback: bool = False


class RagQualityCaseResult(BaseModel):
    """一条质量用例的召回、精确率和兜底判断结果。"""

    case_id: str
    retrieved_chunk_ids: list[str]
    expected_chunk_ids: list[str]
    recall_at_k: float
    precision_at_k: float
    fallback: bool
    passed: bool


class RagQualitySummary(BaseModel):
    """固定问题集的轻量质量汇总。"""

    total_cases: int
    passed_cases: int
    average_recall_at_k: float
    average_precision_at_k: float
    results: list[RagQualityCaseResult]


class ChatResponse(BaseModel):
    """Step 12 返回 citations，并在 session_state 暴露质量摘要。"""

    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    cost_summary: CostSummary
    reasoning_summary: list[str]
    session_state: dict[str, Any]
