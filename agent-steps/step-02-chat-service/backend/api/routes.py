"""FastAPI 路由层，只负责 HTTP 接入和调用 Agent，不承载业务判断。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Any) -> APIRouter:
    """创建 Step 02 的 API 路由，并把请求转交给当前 Agent 实例。"""

    # 项目重点：API 层保持薄，只依赖一个可替换的 Agent provider。
    # 这样测试可以替换当前 Agent，后续步骤也能在不改路由契约的情况下扩展内部能力。
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回当前 step 后端是否已启动。"""

        return {"status": "ok", "step": "02"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台用于点亮或置灰面板的能力声明。"""

        # capabilities 只服务调试后台能力开关，不参与 `/chat` 的业务判断。
        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """接收最小聊天请求，并返回当前 Agent 的结构化响应。"""

        # 路由层只负责请求和响应进出，真正的 Agent 逻辑放在 Step02Agent 里。
        try:
            return agent_provider().chat(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
