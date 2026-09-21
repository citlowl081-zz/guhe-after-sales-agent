"""FastAPI 路由层。

查询改写发生在 Agent 内部，HTTP 入口仍然保持 `/chat` 不变。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter

from agents.customer_service_agent import Step13Agent
from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Callable[[], Step13Agent]) -> APIRouter:
    """创建 Step 13 路由。"""

    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回轻量健康检查。"""

        return {"status": "ok", "step": "13"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台能力声明。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """把聊天请求交给 Agent 编排层。"""

        return agent_provider().chat(request)

    return router
