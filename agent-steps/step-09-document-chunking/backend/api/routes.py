"""FastAPI 路由层。

Step 09 把知识处理变复杂了，但 HTTP 层仍然只做请求转发和能力声明。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter

from agents.customer_service_agent import Step09Agent
from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Callable[[], Step09Agent]) -> APIRouter:
    """创建 Step 09 路由，并延迟取得当前 Agent 实例。"""

    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回轻量健康检查。"""

        return {"status": "ok", "step": "09"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台的能力开关。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """把对话请求交给 Agent 编排层。"""

        return agent_provider().chat(request)

    return router
