"""Pydantic 请求响应模型。早期项目从这里建立稳定 API 契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# reasoning_view 仍然只是调试后台的观察开关，不代表这一版已经有隐藏推理展示。
ReasoningView = Literal["default", "off", "summary", "observe"]


class ChatRequest(BaseModel):
    """古河商城调用 `/chat` 时传入的请求格式。"""

    session_id: str = Field(..., description="当前对话会话 ID")
    # runtime_* 来自系统或调试后台选择的当前用户，不从用户聊天内容里推断。
    runtime_user_id: str = Field(..., description="调试后台当前选择的可信用户 ID")
    runtime_nickname: str | None = Field(default=None, description="调试后台当前选择的用户昵称")
    runtime_member_level: str | None = Field(default=None, description="调试后台当前选择的会员等级")
    runtime_risk_level: str | None = Field(default=None, description="调试后台当前选择的风险等级")
    # user_message 是用户原话。当前版本会把它交给第一版 LLM 客服回答。
    user_message: str = Field(..., description="用户输入的问题")
    reasoning_view: ReasoningView = "default"
    debug: bool = True
    runtime_context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """Step 03 仍然只返回自然语言回答和公开摘要。"""

    session_id: str
    answer: str
    reasoning_summary: list[str]
    session_state: dict[str, Any]
