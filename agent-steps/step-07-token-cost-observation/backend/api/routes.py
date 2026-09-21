"""FastAPI 路由层，只负责 HTTP 接入和调用 Agent，不承载业务判断。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Any) -> APIRouter:
    """创建 Step 07 路由，对外暴露新增的 `cost_summary` 响应契约。"""

    # 项目重点：新增 cost_summary 后，API 层仍只负责暴露契约，不计算 token。
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回当前 step 后端是否已启动。"""

        return {"status": "ok", "step": "07"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台可观察能力声明。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """接收聊天请求，并返回回答、Prompt 状态和成本摘要。"""

        # token 观察在 cost/observer.py，路由层不混入成本计算细节。
        return agent_provider().chat(request)

    return router
