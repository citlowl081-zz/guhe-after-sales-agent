"""Step 25：请求、响应和状态模型。类型集中后，路由、工具、工作流都围绕同一套契约协作。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


ReasoningView = Literal["default", "off", "summary", "observe"]

RiskLevel = Literal["low", "medium", "high"]

RouteSource = Literal["rules", "classifier", "rules_fallback"]

ToolStatus = Literal["success", "skipped"]

NextAction = Literal["answer_user", "ask_clarification", "route_to_controlled_workflow"]

class ChatRequest(BaseModel):
    """ChatRequest 承载本步逐步长出的 Agent 模块能力。"""
    session_id: str = Field(..., description="当前对话会话 ID")
    runtime_user_id: str = Field(..., description="调试后台当前选择的可信用户 ID")
    runtime_nickname: str | None = Field(default=None, description="调试后台当前选择的用户昵称")
    runtime_member_level: str | None = Field(default=None, description="调试后台当前选择的会员等级")
    runtime_risk_level: str | None = Field(default=None, description="调试后台当前选择的风险等级")
    user_message: str = Field(..., description="用户输入的问题")
    reasoning_view: ReasoningView = "default"
    debug: bool = True
    runtime_context: dict[str, Any] | None = None

class RoutePlan(BaseModel):
    """执行器前的结构化路由信号。

    项目重点：RoutePlan 不是模型生成的长计划，而是可校验的入口决策。
    它告诉后续链路要不要 RAG、要不要工具、允许哪些工具、风险有多高，
    以及是否应该交给后续受控售后路径。
    """

    intent: str
    needs_rag: bool
    needs_business_tools: bool
    rag_query: str
    confidence: float
    source: RouteSource
    intents: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    knowledge_domains: list[str] = Field(default_factory=list)
    has_realtime_fact: bool = False
    risk_level: RiskLevel = "low"
    requires_workflow: bool = False
    fallback_policy: str = "default"

class ToolCandidate(BaseModel):
    """ToolCandidate 承载本步逐步长出的 Agent 模块能力。"""
    name: str
    domain: str
    allowed_in_light_path: bool
    risk_level: RiskLevel
    reason: str

class PlannerTrace(BaseModel):
    """PlannerTrace 承载本步逐步长出的 Agent 模块能力。"""
    source: RouteSource
    rule_confidence: float
    candidate_tools: list[ToolCandidate]
    constrained_required_tools: list[str]
    public_reason: str

class ToolAction(BaseModel):
    """ToolAction 承载本步逐步长出的 Agent 模块能力。"""
    tool_name: str
    arguments: dict[str, Any]
    reason: str

class Observation(BaseModel):
    """Observation 承载本步逐步长出的 Agent 模块能力。"""
    tool_name: str
    status: ToolStatus
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    next_action: NextAction

class ToolCallRecord(BaseModel):
    """ToolCallRecord 承载本步逐步长出的 Agent 模块能力。"""
    action: ToolAction
    observation: Observation

class ClarificationCandidate(BaseModel):
    """ClarificationCandidate 承载本步逐步长出的 Agent 模块能力。"""
    value: str
    label: str
    hint: str

class ClarificationRequest(BaseModel):
    """ClarificationRequest 承载本步逐步长出的 Agent 模块能力。"""
    clarification_field: str
    message: str
    candidates: list[ClarificationCandidate] = Field(default_factory=list)

class Citation(BaseModel):
    """Citation 承载本步逐步长出的 Agent 模块能力。"""
    citation_id: str
    source_title: str
    source_path: str
    chunk_id: str
    score: float
    snippet: str

class ChatResponse(BaseModel):
    """ChatResponse 承载本步逐步长出的 Agent 模块能力。"""
    session_id: str
    answer: str
    route_plan: RoutePlan
    planner_trace: PlannerTrace
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    clarification: ClarificationRequest | None = None
    next_action: NextAction
    risk_level: RiskLevel
    needs_human_approval: bool
    reasoning_summary: list[str]
    session_state: dict[str, Any]
