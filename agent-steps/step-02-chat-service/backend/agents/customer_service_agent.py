"""Agent 编排层，承接请求并决定本步要调用哪些模型、Prompt 或观察能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse
from models.llm_client import call_chat_model


class Step02Agent:
    """当前版本的最小 Agent。"""

    def __init__(
        self,
        *,
        chat_http_client: httpx.Client | None = None,
        chat_api_key: str | None = None,
        chat_base_url: str | None = None,
        chat_model_name: str | None = None,
    ) -> None:
        """初始化 Step 02 Agent，并允许测试注入模型 HTTP 客户端。"""

        # 这是最小会话状态，用来证明同一个 session_id 下的请求会被归到同一段对话。
        self._message_count_by_session: dict[str, int] = {}
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理最小聊天请求，证明 `/chat` 已能承接用户消息和系统事实。"""

        # 先更新会话计数，让 session_state 能展示当前会话的最小运行状态。
        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        log_guhe_event("SESSION_MESSAGE_COUNTED", "会话消息计数完成", observe=True, message_count=message_count)

        # 当前版本只把用户原话交给模型；runtime_* 先作为系统状态保留。
        answer = call_chat_model(
            request.user_message,
            http_client=self._chat_http_client,
            api_key=self._chat_api_key,
            base_url=self._chat_base_url,
            model=self._chat_model_name,
        )
        log_guhe_event("CHAT_ANSWER_READY", "模型回答已生成", answer_length=len(answer))
        session_state = {
            "agent_version": "step-02-chat-service",
            "message_count": message_count,
            "runtime_context": {
                # 调试后台用这里验证：系统侧用户事实已经被 Agent 接住。
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "next_gap": "能聊天不代表能接客服；真实业务问题很快会暴露第一版 AI 客服的缺口。",
        }

        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            session_state=session_state,
        )
