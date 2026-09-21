"""Step 25：FastAPI 路由层。HTTP 边界只负责转发，不承载 Agent 决策。"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(chat_handler: Callable[[ChatRequest], Any]) -> APIRouter:
    """创建项目 HTTP 路由。"""

    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回当前步程健康状态。"""

        return {"status": "ok", "step": 'step-25-task-planner-route-plan'}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回当前步程能力开关。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给 Agent 编排。"""

        result = chat_handler(request)
        if inspect.isawaitable(result):
            return await result
        return result

    return router
