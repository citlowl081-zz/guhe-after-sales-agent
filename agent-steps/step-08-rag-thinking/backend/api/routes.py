"""FastAPI 路由只负责把 HTTP 请求交给 Agent。

Step 08 开始出现 RAG，但路由层仍然保持很薄：它不关心怎么检索知识，
只负责暴露 `/health`、`/capabilities` 和 `/chat` 这几个稳定入口。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter

from agents.customer_service_agent import Step08Agent
from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Callable[[], Step08Agent]) -> APIRouter:
    """创建项目后端路由，并通过 provider 取得当前 Agent 实例。"""

    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回轻量健康检查，方便前端和测试确认当前步后端已启动。"""

        return {"status": "ok", "step": "08"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台需要的能力开关。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """把一次客服对话请求交给 Agent 编排层处理。"""

        return agent_provider().chat(request)

    return router
