"""Agent 编排层，承接请求并决定本步要调用哪些模型、Prompt 或观察能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse, Intent, IntentResult
from models.llm_client import call_chat_model
from prompts.loader import load_prompt_registry, render_prompt_template, select_prompt_fragments


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回命中的关键词，用于当前步的轻量粗意图识别。"""

    return [keyword for keyword in keywords if keyword in message]


def classify_intent(user_message: str) -> IntentResult:
    """沿用粗意图规则，为 Prompt Registry 片段选择提供输入信号。"""

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


class Step06Agent:
    """Prompt Registry 版 Agent。"""

    def __init__(
        self,
        *,
        chat_http_client: httpx.Client | None = None,
        chat_api_key: str | None = None,
        chat_base_url: str | None = None,
        chat_model_name: str | None = None,
    ) -> None:
        """初始化 Prompt Registry Agent，并允许测试注入模型调用依赖。"""

        self._message_count_by_session: dict[str, int] = {}
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理一轮聊天请求，按粗意图选择 Prompt 片段后调用模型。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent_result = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent_result.intent)
        registry = load_prompt_registry()
        log_guhe_event("PROMPT_REGISTRY_LOADED", "Prompt注册表已加载", fragment_count=len(registry))
        # 项目重点：Agent 编排层只决定“本轮需要哪些片段”，不把所有规则常驻 Prompt。
        fragments = select_prompt_fragments(intent_result.intent, registry)
        log_guhe_event("PROMPT_FRAGMENTS_SELECTED", "Prompt片段选择完成", observe=True, selected_fragment_ids=[fragment.fragment_id for fragment in fragments], selected_count=len(fragments))
        messages = render_prompt_template(request, intent_result, fragments)
        log_guhe_event("PROMPT_RENDERED", "分层Prompt已装配", observe=True, message_count=len(messages))
        answer = call_chat_model(
            messages,
            http_client=self._chat_http_client,
            api_key=self._chat_api_key,
            base_url=self._chat_base_url,
            model=self._chat_model_name,
        )
        reasoning_summary = [
            "后端先识别粗意图，再从 Prompt Registry 选择当前问题需要的片段。",
            f"本轮加载 {len(fragments)} 个 Prompt 片段，并按 priority 从高到低渲染。",
            "这一版仍然是 Prompt 方案，只是把整面墙拆成可管理的片段。",
        ]
        session_state = {
            "agent_version": "step-06-prompt-registry",
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
                # 调试后台看这些 ID，就能判断答错时是片段没选中、片段被关闭，还是模型没用好。
                "selected_fragment_ids": [fragment.fragment_id for fragment in fragments],
                "selected_fragment_count": len(fragments),
                "priorities": [fragment.priority for fragment in fragments],
                "disabled_fragment_ids": [fragment.fragment_id for fragment in registry if not fragment.enabled],
            },
            "next_gap": "Prompt 片段更好维护，但每轮仍要把规则文本送进模型，成本问题还没有被观察。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent_result.intent,
            intent_result=intent_result,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
