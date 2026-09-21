"""FastAPI 路由层。

向量检索的复杂度在 Agent 和 RAG 层，HTTP 层继续保持稳定入口。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter

from agents.customer_service_agent import Step11Agent
from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Callable[[], Step11Agent]) -> APIRouter:
    """创建 Step 10 路由，并通过 provider 获取当前 Agent。"""

    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回轻量健康检查。"""

        return {"status": "ok", "step": "11"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台能力声明。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """把聊天请求交给 Agent 编排层处理。"""

        return agent_provider().chat(request)

    return router
