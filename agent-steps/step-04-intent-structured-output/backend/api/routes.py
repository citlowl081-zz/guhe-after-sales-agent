"""FastAPI 路由层，只负责 HTTP 接入和调用 Agent，不承载业务判断。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Any) -> APIRouter:
    """创建 Step 04 路由，让结构化意图能力留在 Agent 编排层。"""

    # 项目重点：即使新增了意图识别，API 层仍然只做请求响应进出。
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回当前 step 后端是否已启动。"""

        return {"status": "ok", "step": "04"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台可观察能力声明。"""

        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """接收聊天请求，并返回 answer 与第一版 intent 结构化结果。"""

        # 路由层保持薄，只把已校验的 ChatRequest 交给当前 Agent。
        return agent_provider().chat(request)

    return router
