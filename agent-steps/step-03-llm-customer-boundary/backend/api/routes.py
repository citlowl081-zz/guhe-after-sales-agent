"""FastAPI 路由层，只负责 HTTP 接入和调用 Agent，不承载业务判断。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_agent_capabilities


def create_router(agent_provider: Any) -> APIRouter:
    """创建 Step 03 路由，保持 API 入口与 LLM 客服编排分离。"""

    # 项目重点：路由层不拼 Prompt、不调用模型，只把已校验请求交给 Agent 编排层。
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        """返回当前 step 后端是否已启动。"""

        return {"status": "ok", "step": "03"}

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        """返回调试后台可观察能力，不影响 Agent 执行路径。"""

        # 调试后台根据这里决定显示哪些面板；它不是 Agent 的执行依据。
        return load_agent_capabilities()

    @router.post("/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        """把聊天请求交给第一版 LLM 客服，并返回公开摘要。"""

        # 路由层只做接口进出，第一版客服边界放在 Step03Agent 中。
        return agent_provider().chat(request)

    return router
