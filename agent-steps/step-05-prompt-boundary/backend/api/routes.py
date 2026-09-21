"""FastAPI 路由层，只负责 HTTP 接入和调用 Agent，不承载业务判断。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Any) -> APIRouter:
    """创建 Step 05 路由，避免把 Prompt 复杂度泄漏到 API 层。"""

    # 项目重点：Prompt 变复杂以后，更要避免把业务编排塞进 API 函数。
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回当前 step 后端是否已启动。"""

        return {"status": "ok", "step": "05"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台可观察能力声明。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """接收聊天请求，并返回受 Prompt 边界约束的回答。"""

        # API 层只认 ChatResponse 契约；全量 Prompt 注入发生在 Agent/Prompt 模块。
        return agent_provider().chat(request)

    return router
