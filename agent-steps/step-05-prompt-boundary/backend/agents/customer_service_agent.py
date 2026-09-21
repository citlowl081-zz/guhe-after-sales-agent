"""Agent 编排层，承接请求并决定本步要调用哪些模型、Prompt 或观察能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse, Intent, IntentResult
from models.llm_client import call_chat_model
from prompts.loader import FULL_POLICY_DOCUMENTS, build_full_context_messages, detect_context_conflicts, estimate_tokens


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回命中的关键词，用于沿用 Step 04 的粗意图规则。"""

    return [keyword for keyword in keywords if keyword in message]


def classify_intent(user_message: str) -> IntentResult:
    """沿用第一版粗分拣，让 Prompt 改造接在已经讲过的 intent 后面。"""

    message = user_message.strip().lower()
    intent_rules: list[tuple[Intent, list[str], str]] = [
        ("complaint", ["投诉", "举报", "赔偿", "曝光", "315", "别踢皮球"], "用户表达了投诉、赔偿或强烈不满，先标记为投诉类消息。"),
        ("refund_request", ["退款", "退货", "取消订单", "坏了", "无法开机", "质量问题"], "用户在询问退款、退货或质量问题，先标记为售后退款类消息。"),
        ("order_query", ["订单", "物流", "快递", "发货", "到哪", "运单"], "用户在询问订单或物流状态，先标记为订单查询类消息。"),
        ("promotion_consult", ["优惠", "活动", "会员价", "券", "满减", "折扣"], "用户在询问优惠或活动，先标记为活动咨询类消息。"),
        ("product_consult", ["耳机", "充电器", "音箱", "推荐", "哪个好"], "用户在询问商品或推荐，先标记为商品咨询类消息。"),
        ("general_chat", ["你好", "您好", "在吗", "谢谢"], "用户只是普通问候，先标记为普通聊天。"),
    ]

    for intent, keywords, explanation in intent_rules:
        matched = first_matched_keywords(message, keywords)
        if matched:
            return IntentResult(intent=intent, matched_keywords=matched, explanation=explanation)
    return IntentResult(intent="unknown", matched_keywords=[], explanation="没有命中当前版本的粗意图规则。")


class Step05Agent:
    """Prompt 边界与全量规则注入版 Agent。"""

    def __init__(
        self,
        *,
        chat_http_client: httpx.Client | None = None,
        chat_api_key: str | None = None,
        chat_base_url: str | None = None,
        chat_model_name: str | None = None,
    ) -> None:
        """初始化 Prompt 边界版 Agent，并允许测试注入模型调用依赖。"""

        self._message_count_by_session: dict[str, int] = {}
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理聊天请求，把粗意图、全量规则上下文和模型回答串成一轮响应。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent_result = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent_result.intent, matched_keywords=intent_result.matched_keywords)
        conflicts = detect_context_conflicts(request.user_message)
        log_guhe_event("PROMPT_CONFLICTS_DETECTED", "Prompt上下文冲突检查完成", observe=True, conflict_count=len(conflicts), conflicts=conflicts)
        # 项目重点：本步故意走“全量规则进 Prompt”，让后面转向 Prompt Registry/RAG 有真实动机。
        messages = build_full_context_messages(request, intent_result, FULL_POLICY_DOCUMENTS, conflicts)
        prompt_text = "\n".join(message["content"] for message in messages)
        log_guhe_event("FULL_PROMPT_RENDERED", "全量规则Prompt已装配", observe=True, document_count=len(FULL_POLICY_DOCUMENTS), estimated_tokens=estimate_tokens(prompt_text), message_count=len(messages))
        answer = call_chat_model(
            messages,
            http_client=self._chat_http_client,
            api_key=self._chat_api_key,
            base_url=self._chat_base_url,
            model=self._chat_model_name,
        )
        reasoning_summary = [
            "后端沿用 Step 04 粗意图识别，先知道用户问题属于哪一类。",
            "这一版先用 system prompt 写清客服身份、事实优先级和回答边界。",
            f"老板追问规则依据后，代码把 {len(FULL_POLICY_DOCUMENTS)} 份规则文档全量塞进 Prompt。",
            f"本轮检测到 {len(conflicts)} 条上下文冲突线索，说明长 Prompt 开始变乱。",
        ]
        session_state = {
            "agent_version": "step-05-prompt-boundary-full-context",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "prompt_boundary": {
                "mode": "system_prompt_fact_priority_and_refusal_rules",
                "fact_priority": ["runtime_facts", "current_policy_documents", "legacy_documents", "user_claims", "model_general_knowledge"],
                "boundary_rule_count": 4,
            },
            "prompt_context": {
                "mode": "full_document_injection",
                "document_count": len(FULL_POLICY_DOCUMENTS),
                "document_ids": [document.doc_id for document in FULL_POLICY_DOCUMENTS],
                "estimated_prompt_tokens": estimate_tokens(prompt_text),
                # conflict_count 是观察信号，不是规则裁决器；本步还不会自动决定新旧规则谁赢。
                "conflict_count": len(conflicts),
                "conflicts": [conflict.model_dump() for conflict in conflicts],
            },
            "next_gap": "system prompt 能先堵住乱承诺，全量 Prompt 能让规则进模型，但旧活动和新活动会互相干扰。",
        }

        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent_result.intent,
            intent_result=intent_result,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
