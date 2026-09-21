"""Agent 编排层，承接请求并决定本步要调用哪些模型、Prompt 或观察能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse, Intent, IntentResult
from cost.observer import build_cost_summary
from models.llm_client import call_chat_model
from prompts.loader import load_prompt_registry, render_prompt_template, select_prompt_fragments


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回命中的关键词，用于当前步的轻量粗意图识别。"""

    return [keyword for keyword in keywords if keyword in message]


def classify_intent(user_message: str) -> IntentResult:
    """沿用粗意图规则，为 Prompt 片段选择和成本日志提供分类信号。"""

    message = user_message.strip().lower()
    intent_rules: list[tuple[Intent, list[str], str]] = [
        ("complaint", ["投诉", "举报", "赔偿", "曝光", "315", "别踢皮球"], "用户表达了投诉或强烈不满。"),
        ("refund_request", ["退款", "退货", "取消订单", "坏了", "无法开机", "质量问题"], "用户在询问退款、退货或质量问题。"),
        ("order_query", ["订单", "物流", "快递", "发货", "到哪", "运单"], "用户在询问订单或物流状态。"),
        ("promotion_consult", ["优惠", "活动", "会员价", "券", "满减", "折扣"], "用户在询问优惠或活动。"),
        ("product_consult", ["耳机", "充电器", "音箱", "推荐", "哪个好"], "用户在询问商品或推荐。"),
        ("general_chat", ["你好", "您好", "在吗", "谢谢"], "用户只是普通问候。"),
    ]
    for intent, keywords, explanation in intent_rules:
        matched = first_matched_keywords(message, keywords)
        if matched:
            return IntentResult(intent=intent, matched_keywords=matched, explanation=explanation)
    return IntentResult(intent="unknown", matched_keywords=[], explanation="没有命中当前版本的粗意图规则。")


class Step07Agent:
    """带 token 成本观察的 Prompt Registry Agent。"""

    def __init__(
        self,
        *,
        chat_http_client: httpx.Client | None = None,
        chat_api_key: str | None = None,
        chat_base_url: str | None = None,
        chat_model_name: str | None = None,
    ) -> None:
        """初始化成本观察版 Agent，并准备会话级 token 观察日志。"""

        self._message_count_by_session: dict[str, int] = {}
        self._cost_events_by_session: dict[str, list[dict]] = {}
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理一轮聊天请求，并在模型回答后生成 `cost_summary`。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent_result = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent_result.intent)
        registry = load_prompt_registry()
        fragments = select_prompt_fragments(intent_result.intent, registry)
        log_guhe_event("PROMPT_FRAGMENTS_SELECTED", "Prompt片段选择完成", selected_count=len(fragments), selected_fragment_ids=[fragment.fragment_id for fragment in fragments])
        messages = render_prompt_template(request, intent_result, fragments)
        log_guhe_event("PROMPT_RENDERED", "模型消息已装配", message_count=len(messages))
        # 模型调用先返回 answer 和可选 usage；后面的 cost_summary 只观察成本，不影响答案生成。
        model_result = call_chat_model(
            messages,
            http_client=self._chat_http_client,
            api_key=self._chat_api_key,
            base_url=self._chat_base_url,
            model=self._chat_model_name,
        )
        # 项目重点：成本观察从模型返回和 Prompt 内容里计算，不混进客服回答逻辑。
        cost_summary = build_cost_summary(messages, model_result.answer, model_result.usage)
        log_guhe_event("COST_OBSERVED", "Token成本观察完成", observe=True, prompt_tokens=cost_summary.prompt_tokens, answer_tokens=cost_summary.answer_tokens, total_tokens=cost_summary.total_tokens, token_source=cost_summary.token_source)
        event = {
            "message_count": message_count,
            "intent": intent_result.intent,
            "selected_fragment_ids": [fragment.fragment_id for fragment in fragments],
            "cost_summary": cost_summary.model_dump(),
        }
        self._cost_events_by_session.setdefault(request.session_id, []).append(event)
        reasoning_summary = [
            "后端先渲染当前 Prompt Registry，再观察 Prompt 和回答 token。",
            f"本轮 token 来源为 {cost_summary.token_source}，总 token 为 {cost_summary.total_tokens}，并写入会话日志。",
            "token 观察说明 Prompt 仍在每轮重复发送规则文本，下一步需要减少无关上下文。",
        ]
        session_state = {
            "agent_version": "step-07-token-cost-observation",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "prompt_registry": {
                "template": "customer_service_v1",
                "selected_fragment_ids": [fragment.fragment_id for fragment in fragments],
                "selected_fragment_count": len(fragments),
            },
            "cost_log": {
                "event_count": len(self._cost_events_by_session[request.session_id]),
                "latest": event,
            },
            "next_gap": "Prompt 已经救过火，但不能继续把所有资料都塞进去；下一幕要让 Agent 只找相关资料。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=model_result.answer,
            intent=intent_result.intent,
            intent_result=intent_result,
            cost_summary=cost_summary,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
