"""Step 21：请求、响应与项目状态模型。把类型集中放在这里，是从单文件脚本迈向接口契约的第一步。"""

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

NextAction = Literal["answer_user", "ask_clarification", "fallback_answer", "transfer_to_human"]

RiskLevel = Literal["low", "medium", "high"]

ErrorCategory = Literal[
    "none",
    "timeout",
    "validation_error",
    "not_found",
    "forbidden",
    "business_error",
    "model_unavailable",
    "system_error",
    "high_risk_write_blocked",
]
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
    read_only: bool
    risk_level: RiskLevel

class ToolAction(BaseModel):
    """ToolAction 项目对象，承载本步逐步长出的 Agent 能力。"""
    tool_name: str
    arguments: dict[str, Any]
    reason: str

class ToolResult(BaseModel):
    """ToolResult 项目对象，承载本步逐步长出的 Agent 能力。"""
    tool_name: str
    status: ToolStatus
    raw_payload: dict[str, Any]
    attempts: int = 1
    error_category: ErrorCategory = "none"

class Observation(BaseModel):
    """Observation 项目对象，承载本步逐步长出的 Agent 能力。"""
    tool_name: str
    status: ToolStatus
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    omitted_fields: list[str] = Field(default_factory=list)
    next_action: NextAction
    error_category: ErrorCategory = "none"

class ToolCallRecord(BaseModel):
    """ToolCallRecord 项目对象，承载本步逐步长出的 Agent 能力。"""
    action: ToolAction
    observation: Observation
    attempts: int

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

class DegradationState(BaseModel):
    """DegradationState 项目对象，承载本步逐步长出的 Agent 能力。"""
    degraded: bool
    error_category: ErrorCategory
    retry_count: int = 0
    fallback_message: str | None = None

class ChatResponse(BaseModel):
    """ChatResponse 项目对象，承载本步逐步长出的 Agent 能力。"""
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    clarification: ClarificationRequest | None = None
    next_action: NextAction
    risk_level: RiskLevel
    needs_human_approval: bool
    degraded: bool
    reasoning_summary: list[str]
    session_state: dict[str, Any]

class ToolExecutionError(Exception):
    """ToolExecutionError 项目对象，承载本步逐步长出的 Agent 能力。"""
    def __init__(self, category: ErrorCategory, message: str) -> None:
        """执行 __init__ 对应的项目逻辑。"""
        super().__init__(message)
        self.category = category

class ModelServiceError(Exception):
    """ModelServiceError 项目对象，承载本步逐步长出的 Agent 能力。"""
    pass
