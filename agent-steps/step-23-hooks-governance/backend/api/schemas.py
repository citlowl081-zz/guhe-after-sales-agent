"""Step 23：请求、响应和状态模型。类型集中后，路由、工具、工作流都围绕同一套契约协作。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


ReasoningView = Literal["default", "off", "summary", "observe"]

Intent = Literal["general_chat", "product_consult", "order_query", "refund_status_query", "refund_request", "unknown"]

ToolStatus = Literal["success", "error", "skipped"]

NextAction = Literal["answer_user", "ask_clarification", "fallback_answer", "transfer_to_human"]

RiskLevel = Literal["low", "medium", "high"]

ErrorCategory = Literal["none", "timeout", "not_found", "forbidden", "model_unavailable", "high_risk_write_blocked"]

HookType = Literal["pre_tool_call", "post_tool_call", "on_error", "on_completion"]

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

class ToolSpec(BaseModel):
    """ToolSpec 承载本步逐步长出的 Agent 模块能力。"""
    name: str
    description: str
    required: list[str]
    parameters_schema: dict[str, str]
    read_only: bool
    risk_level: RiskLevel

class ToolAction(BaseModel):
    """ToolAction 承载本步逐步长出的 Agent 模块能力。"""
    tool_name: str
    arguments: dict[str, Any]
    reason: str

class ToolResult(BaseModel):
    """ToolResult 承载本步逐步长出的 Agent 模块能力。"""
    tool_name: str
    status: ToolStatus
    raw_payload: dict[str, Any]
    attempts: int = 1
    error_category: ErrorCategory = "none"

class Observation(BaseModel):
    """Observation 承载本步逐步长出的 Agent 模块能力。"""
    tool_name: str
    status: ToolStatus
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    omitted_fields: list[str] = Field(default_factory=list)
    next_action: NextAction
    error_category: ErrorCategory = "none"

class ToolCallRecord(BaseModel):
    """ToolCallRecord 承载本步逐步长出的 Agent 模块能力。"""
    action: ToolAction
    observation: Observation
    attempts: int

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

class DegradationState(BaseModel):
    """DegradationState 承载本步逐步长出的 Agent 模块能力。"""
    degraded: bool
    error_category: ErrorCategory
    retry_count: int = 0
    fallback_message: str | None = None

class HookEvent(BaseModel):
    """HookEvent 承载本步逐步长出的 Agent 模块能力。"""
    hook_type: HookType
    target_name: str
    action: str
    result: str
    reason: str
    safe_summary: dict[str, Any]
    redacted: bool = False
    degraded: bool = False

class HookCompletion(BaseModel):
    """HookCompletion 承载本步逐步长出的 Agent 模块能力。"""
    hook_count: int
    tool_count: int
    touched_tools: list[str]
    redacted_count: int
    degraded_count: int
    risk_hit_count: int
    safe_summary: dict[str, Any]

class ChatResponse(BaseModel):
    """ChatResponse 承载本步逐步长出的 Agent 模块能力。"""
    session_id: str
    answer: str
    intent: Intent
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    hook_events: list[HookEvent]
    hook_completion: HookCompletion
    clarification: ClarificationRequest | None = None
    next_action: NextAction
    risk_level: RiskLevel
    needs_human_approval: bool
    degraded: bool
    reasoning_summary: list[str]
    session_state: dict[str, Any]

class ToolExecutionError(Exception):
    """ToolExecutionError 承载本步逐步长出的 Agent 模块能力。"""
    def __init__(self, category: ErrorCategory, message: str) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        super().__init__(message)
        self.category = category

class ModelServiceError(Exception):
    """ModelServiceError 承载本步逐步长出的 Agent 模块能力。"""
    pass
