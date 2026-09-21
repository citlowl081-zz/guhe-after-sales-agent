"""Step 08 的请求、响应和 RAG 数据结构。

这些 Pydantic 模型是模块之间的契约：API、Agent、RAG 和成本观察都通过
明确结构传递数据，而不是把临时 dict 一路传到底。
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
    """粗意图识别结果，先给 RAG 一个很轻的检索方向。"""

    intent: Intent
    matched_keywords: list[str] = Field(default_factory=list)
    explanation: str


class SourceDocument(BaseModel):
    """知识库里的 Markdown 原文。"""

    source_path: str
    title: str
    metadata: dict[str, Any]
    body: str


class KnowledgeSection(BaseModel):
    """从 Markdown 文档中粗读出来的业务章节。"""

    source_path: str
    document_title: str
    section_index: int
    section: str
    snippet_id: str | None = None
    keywords: list[str] = Field(default_factory=list)
    effective_status: str = "active"
    text: str


class KnowledgeSnippet(BaseModel):
    """Step 08 最小知识片段；chunk 和引用来源会在 RAG 链路里继续补齐。"""

    snippet_id: str
    title: str
    topic: str
    keywords: list[str]
    effective_status: str = "active"
    text: str


class KnowledgeHit(BaseModel):
    """一次检索命中的知识片段及其调试信息。"""

    snippet: KnowledgeSnippet
    score: float = Field(ge=0, le=1)
    matched_keywords: list[str]


class CostSummary(BaseModel):
    """把 Prompt 和回答长度转成可观察的成本摘要。"""

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
