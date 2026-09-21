"""Step 19：请求、响应与项目状态模型。把类型集中放在这里，是从单文件脚本迈向接口契约的第一步。"""

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
    "refund_status_query",
    "complaint",
    "unknown",
]

ToolStatus = Literal["success", "error", "skipped"]
KnowledgeTopic = Literal["promotion", "product", "after_sale", "shipping", "faq"]
RetrievalScene = Literal["promotion", "after_sale", "shipping", "product", "unknown"]
KnowledgeStatus = Literal["current", "expired", "reference"]

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

class ToolSpec(BaseModel):
    """ToolSpec 项目对象，承载本步逐步长出的 Agent 能力。"""
    name: str
    description: str
    required: list[str]
    parameters_schema: dict[str, str]

class ToolAction(BaseModel):
    """ToolAction 项目对象，承载本步逐步长出的 Agent 能力。"""
    tool_name: str
    arguments: dict[str, Any]
    reason: str

class ToolObservation(BaseModel):
    """ToolObservation 项目对象，承载本步逐步长出的 Agent 能力。"""
    tool_name: str
    status: ToolStatus
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)

class ToolCallRecord(BaseModel):
    """ToolCallRecord 项目对象，承载本步逐步长出的 Agent 能力。"""
    action: ToolAction
    observation: ToolObservation

class ClarificationCandidate(BaseModel):
    """ClarificationCandidate 项目对象，承载本步逐步长出的 Agent 能力。"""
    value: str
    label: str
    hint: str

class ClarificationRequest(BaseModel):
    """ClarificationRequest 项目对象，承载本步逐步长出的 Agent 能力。"""
    clarification_field: str
    message: str
    candidates: list[ClarificationCandidate] = Field(default_factory=list)

class ClarificationPlan(BaseModel):
    """LLM 输出的澄清规划草案。

    项目重点：模型负责理解用户话术、抽取字段和提出澄清问题；
    后端仍要用 ToolSpec、当前用户和业务工具结果做硬校验。
    """

    intent: Intent
    tool_name: str | None = None
    known_arguments: dict[str, Any] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    confidence: float = 0.7
    reason: str = "LLM 规划澄清和工具候选。"
    source: Literal["llm"] = "llm"
    model_name: str | None = None

class Citation(BaseModel):
    """Citation 项目对象，承载本步逐步长出的 Agent 能力。"""
    citation_id: str
    source_title: str
    source_path: str
    chunk_id: str
    score: float
    snippet: str

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

class ChatResponse(BaseModel):
    """ChatResponse 项目对象，承载本步逐步长出的 Agent 能力。"""
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    clarification: ClarificationRequest | None = None
    reasoning_summary: list[str]
    session_state: dict[str, Any]
