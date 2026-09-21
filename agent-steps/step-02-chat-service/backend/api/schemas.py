"""Pydantic 请求响应模型。早期项目从这里建立稳定 API 契约。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """调试后台发送给 `/chat` 的最小请求格式。"""

    session_id: str = Field(..., description="当前对话会话 ID")
    # runtime_* 是系统确认过的事实，不能让用户在聊天里自称会员或冒充身份。
    runtime_user_id: str = Field(..., description="调试后台当前选择的可信用户 ID")
    runtime_nickname: str | None = Field(default=None, description="调试后台当前选择的用户昵称")
    runtime_member_level: str | None = Field(default=None, description="调试后台当前选择的会员等级")
    runtime_risk_level: str | None = Field(default=None, description="调试后台当前选择的风险等级")
    # user_message 才是真正交给模型的用户输入，必须和 runtime_* 分开放。
    user_message: str = Field(..., description="用户输入的问题")
    runtime_context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """`/chat` 返回给调试后台的最小结构化响应。"""

    session_id: str
    answer: str
    session_state: dict[str, Any]
