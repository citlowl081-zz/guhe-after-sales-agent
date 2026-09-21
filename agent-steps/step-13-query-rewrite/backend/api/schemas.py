"""Step 13 数据结构。

本步新增 `QueryRewrite`，把“用户原话”和“检索用问题”分开。
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
KnowledgeTopic = Literal["promotion", "product", "after_sale", "shipping"]
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


class KnowledgeHit(BaseModel):
    """一次向量检索命中的知识片段和相似度分数。"""

    chunk: KnowledgeChunk
    score: float = Field(ge=0, le=1)
    matched_keywords: list[str] = Field(default_factory=list)


class VectorRecord(BaseModel):
    """进程内向量库保存的一条知识向量记录。"""

    chunk: KnowledgeChunk
    embedding: list[float]


class QueryRewrite(BaseModel):
    """检索改写结果，保留原话和改写后的查询。"""

    original_query: str
    rewritten_query: str
    applied: bool
    added_terms: list[str]
    reason: str


class Citation(BaseModel):
    """返回给调试后台的引用证据。"""

    citation_id: str
    source_title: str
    source_path: str
    chunk_id: str
    score: float
    snippet: str


class ChatResponse(BaseModel):
    """Step 13 聊天响应。"""

    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    reasoning_summary: list[str]
    session_state: dict[str, Any]
