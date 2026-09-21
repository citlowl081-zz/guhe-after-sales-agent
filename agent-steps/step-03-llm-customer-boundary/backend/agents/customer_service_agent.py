"""Agent 编排层，承接请求并决定本步要调用哪些模型、Prompt 或观察能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse
from models.llm_client import call_chat_model


def build_customer_service_messages(request: ChatRequest) -> list[dict[str, str]]:
    """把业务问题包装成第一版 AI 客服能接收的 messages。"""

    # 项目重点：system message 只能声明身份和边界，不能凭空变出活动、订单或退款事实。
    system_message = (
        "你是古河商城公司的第一版 AI 客服。请用自然、耐心的客服语气回答用户问题。"
        "当前版本只接入了大模型聊天能力，还没有接入古河商城的活动规则、订单物流、"
        "退款条件、售后流程或业务工具。"
    )
    # Step 03 只暴露 LLM-only 边界：模型先看用户原话，runtime_* 继续留在 session_state。
    user_message = "用户问题：\n" + request.user_message

    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message},
    ]


class Step03Agent:
    """第一版 LLM 客服。"""

    def __init__(
        self,
        *,
        chat_http_client: httpx.Client | None = None,
        chat_api_key: str | None = None,
        chat_base_url: str | None = None,
        chat_model_name: str | None = None,
    ) -> None:
        """初始化第一版 LLM 客服，并保留可注入的模型调用依赖。"""

        # 继续沿用最小会话状态，证明同一 session_id 下的请求会进入同一段对话。
        self._message_count_by_session: dict[str, int] = {}
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """把用户业务问题交给 LLM，并公开说明当前还没有业务事实来源。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]

        # 项目重点：模型只拿到客服身份和用户原话，还没有任何可验证的业务依据。
        messages = build_customer_service_messages(request)
        log_guhe_event("PROMPT_BOUNDARY_READY", "客服Prompt边界已装配", observe=True, message_count=len(messages), business_tools=False)
        answer = call_chat_model(
            messages,
            http_client=self._chat_http_client,
            api_key=self._chat_api_key,
            base_url=self._chat_base_url,
            model=self._chat_model_name,
        )
        log_guhe_event("LLM_ONLY_ANSWER_READY", "纯模型客服回答已生成", observe=True, answer_source="llm_only", answer_length=len(answer))
        reasoning_summary = [
            "后端接收 ChatRequest，并把用户问题包装成第一版客服 messages。",
            "模型会生成自然语言客服回答，但当前版本没有活动、订单、物流或退款事实来源。",
            "从 ReAct 缺口看，这一版还没有真正的 Action 和 Observation。",
            "这一版响应不能被业务系统当成处理结果。",
        ]
        session_state = {
            "agent_version": "step-03-llm-customer-boundary",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "llm_customer_boundary": {
                "answer_source": "llm_only",
                "promotion_rules": "not_connected",
                "order_logistics": "not_connected",
                "refund_policy": "not_connected",
                "business_tools": "not_connected",
            },
            "next_gap": "AI 能说客服话，不代表系统已经知道用户问题属于哪类业务请求。",
        }

        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
