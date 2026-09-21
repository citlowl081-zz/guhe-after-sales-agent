"""Step 16 数据结构。

本步新增 `KnowledgeIndex` 和 `RetrievalCacheEntry`，用于索引版本和检索缓存。
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
KnowledgeTopic = Literal["promotion", "product", "after_sale", "shipping", "faq"]
RetrievalScene = Literal["promotion", "after_sale", "shipping", "product", "unknown"]
KnowledgeStatus = Literal["current", "expired", "reference"]


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


class KnowledgeChunk(BaseModel):
    """从 knowledge_chunks.json 读取的可检索知识片段。"""

    chunk_id: str
    title: str
    source_path: str
    section: str
    topic: KnowledgeTopic
    status: KnowledgeStatus
    keywords: list[str]
    text: str


class KnowledgeIndex(BaseModel):
    """内存中的知识索引快照。"""

    version: str
    fingerprint: str
    chunk_count: int
    chunks_by_id: dict[str, KnowledgeChunk]
    inverted_index: dict[str, list[str]]


class RetrievalPlan(BaseModel):
    """检索前计划：场景、主题范围和关键词补充。"""

    original_query: str
    rewritten_query: str
    scene: RetrievalScene
    allowed_topics: list[KnowledgeTopic]
    keyword_terms: list[str]
    reason: str


class KnowledgeHit(BaseModel):
    """Hybrid RAG 命中的知识片段和多路召回分数。"""

    chunk: KnowledgeChunk
    score: float = Field(ge=0, le=1)
    vector_score: float = Field(default=0, ge=0, le=1)
    keyword_score: float = Field(default=0, ge=0, le=1)
    sources: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    rerank_reasons: list[str] = Field(default_factory=list)


class RetrievalCacheEntry(BaseModel):
    """RAG 检索缓存条目，只保存命中结果，不保存最终回答。"""

    index_version: str
    plan: RetrievalPlan
    hits: list[KnowledgeHit]
    retrieval_debug: dict[str, Any]


class Citation(BaseModel):
    """返回给调试后台的引用证据。"""

    citation_id: str
    source_title: str
    source_path: str
    chunk_id: str
    score: float
    snippet: str


class ChatResponse(BaseModel):
    """Step 16 聊天响应。"""

    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    reasoning_summary: list[str]
    session_state: dict[str, Any]
