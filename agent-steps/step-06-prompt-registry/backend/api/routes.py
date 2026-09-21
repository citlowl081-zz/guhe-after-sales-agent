"""FastAPI 路由层，只负责 HTTP 接入和调用 Agent，不承载业务判断。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Any) -> APIRouter:
    """创建 Step 06 路由，对外保持 `/chat`，对内使用 Prompt Registry。"""

    # 项目重点：Prompt Registry 是内部实现，外部调用方仍然只看稳定的 `/chat`。
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回当前 step 后端是否已启动。"""

        return {"status": "ok", "step": "06"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台可观察能力声明。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """接收聊天请求，并返回本轮 Prompt Registry 选择结果。"""

        # 路由层不选择 Prompt 片段，避免入口函数变成新的一面 Prompt 墙。
        return agent_provider().chat(request)

    return router
