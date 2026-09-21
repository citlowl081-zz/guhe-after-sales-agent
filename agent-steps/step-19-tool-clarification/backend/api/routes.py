"""Step 19：FastAPI 路由层。路由只负责 HTTP 边界，把 Agent 编排留给业务模块。"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Callable[[], Any]) -> APIRouter:
    """创建项目 HTTP 路由，保持入口文件轻量。"""

    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回当前步程后端健康状态。"""

        return {"status": "ok", "step": '19'}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回当前步程的能力开关。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给项目 Agent 编排。"""

        return agent_provider().chat(request)

    return router
