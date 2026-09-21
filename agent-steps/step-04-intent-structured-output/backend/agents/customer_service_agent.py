"""Agent 编排层，承接请求并决定本步要调用哪些模型、Prompt 或观察能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import re

import httpx

from api.schemas import ChatRequest, ChatResponse, Intent, IntentResult
from models.answer_client import compose_grounded_answer
from models.classifier_client import classify_intent_with_model


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回命中的关键词，方便调试后台解释这一版分拣器为什么这么判断。"""

    return [keyword for keyword in keywords if keyword in message]


INTENT_RULES: list[tuple[Intent, list[str], str]] = [
    ("complaint", ["投诉", "举报", "赔偿", "曝光", "315", "别踢皮球"], "用户表达了投诉、赔偿或强烈不满，规则高置信标记为投诉类消息。"),
    ("refund_request", ["退款", "退货", "取消订单", "坏了", "无法开机", "质量问题"], "用户在询问退款、退货或质量问题，规则高置信标记为售后退款类消息。"),
    ("order_query", ["订单", "物流", "快递", "发货", "到哪", "运单"], "用户在询问订单或物流状态，规则高置信标记为订单查询类消息。"),
    ("promotion_consult", ["优惠", "活动", "会员价", "券", "满减", "折扣"], "用户在询问优惠或活动，规则高置信标记为活动咨询类消息。"),
    ("product_consult", ["耳机", "充电器", "音箱", "推荐", "哪个好"], "用户在询问商品或推荐，规则高置信标记为商品咨询类消息。"),
    ("general_chat", ["你好", "您好", "在吗", "谢谢"], "用户只是普通问候，规则高置信标记为普通聊天。"),
]

# 实体词只说明用户谈到了什么，不足以单独证明用户要走哪条业务路径。
CONTEXT_KEYWORDS: dict[Intent, set[str]] = {
    "order_query": {"订单"},
    "product_consult": {"耳机", "充电器", "音箱"},
}

NEGATION_PREFIX_PATTERN = re.compile(
    r"(?:不是|并非|不想|不打算|不需要|无需|不用|不要)(?:想要|想|要|申请|办理)?$"
)
REFUND_GOAL_PATTERN = re.compile(
    r"(?:直接|马上|立刻|赶紧|帮我|给我|我要|我想|申请|办理|能否|能不能|可以|是否|还能)"
    r"[^，。！？]{0,8}(?:退款|退货|取消订单|退钱)|(?:退款|退货|取消订单|退钱)(?:吗|么|吧|！|。|$)"
)


def is_negated_keyword(message: str, keyword: str) -> bool:
    """识别“不是要退款”一类否定线索，避免把被否定的词当成高置信目标。"""

    start = message.find(keyword)
    found = False
    while start >= 0:
        found = True
        prefix = message[max(0, start - 8):start]
        if not NEGATION_PREFIX_PATTERN.search(prefix):
            return False
        start = message.find(keyword, start + len(keyword))
    return found


def build_rule_evidence(message: str) -> list[tuple[Intent, list[str], list[str], str]]:
    """收集每个意图的有效词、否定词和说明，先看全局证据再决定是否高置信。"""

    evidence = []
    for intent, keywords, explanation in INTENT_RULES:
        matched = first_matched_keywords(message, keywords)
        negated = [keyword for keyword in matched if is_negated_keyword(message, keyword)]
        active = [keyword for keyword in matched if keyword not in negated]
        if matched:
            evidence.append((intent, active, negated, explanation))
    return evidence


def plan_intent_by_rules(user_message: str) -> IntentResult | None:
    """先用确定性规则处理高置信客服场景。"""

    message = user_message.strip().lower()
    evidence = build_rule_evidence(message)
    active_evidence = [item for item in evidence if item[1]]
    negated_keywords = [keyword for _, _, negated, _ in evidence for keyword in negated]

    # 明确投诉仍按项目既有顺序优先；它只是分拣信号，不代表已经转人工或批准赔偿。
    complaint = next((item for item in active_evidence if item[0] == "complaint"), None)
    if complaint:
        return IntentResult(
            intent="complaint",
            source="rules",
            confidence=0.95,
            matched_keywords=complaint[1],
            explanation=complaint[3],
        )

    # “物流太慢，直接退款”虽然包含物流背景，但明确动作目标仍然是退款。
    refund = next((item for item in active_evidence if item[0] == "refund_request"), None)
    if refund and REFUND_GOAL_PATTERN.search(message):
        return IntentResult(
            intent="refund_request",
            source="rules",
            confidence=0.95,
            matched_keywords=refund[1],
            explanation=refund[3],
        )

    core_evidence = [
        item
        for item in active_evidence
        if any(keyword not in CONTEXT_KEYWORDS.get(item[0], set()) for keyword in item[1])
    ]
    if negated_keywords and not active_evidence:
        return IntentResult(
            intent="unknown",
            source="rules",
            confidence=0.65,
            matched_keywords=[],
            explanation="规则只发现被明确否定的意图，不能把它当成用户真实诉求，交给分类模型复核。",
        )
    if len(core_evidence) > 1 or (not core_evidence and len(active_evidence) > 1) or negated_keywords:
        primary = core_evidence[0] if core_evidence else active_evidence[0]
        intents = "、".join(item[0] for item in core_evidence or active_evidence)
        return IntentResult(
            intent=primary[0],
            source="rules",
            confidence=0.65,
            matched_keywords=primary[1],
            explanation=f"规则发现多个意图或否定表达（{intents}），证据不足以高置信直出，交给分类模型复核。",
        )

    if len(active_evidence) == 1:
        only_intent, active, _, _ = active_evidence[0]
        if not core_evidence:
            return IntentResult(
                intent=only_intent,
                source="rules",
                confidence=0.72,
                matched_keywords=active,
                explanation="当前只命中商品或订单等上下文实体，尚不能高置信判断用户真正诉求。",
            )

    # 通过前置证据检查后，继续沿用项目正文中的规则循环和高置信返回结构。
    for intent, keywords, explanation in INTENT_RULES:
        matched = first_matched_keywords(message, keywords)
        if matched:
            return IntentResult(
                intent=intent,
                source="rules",
                confidence=0.95,
                matched_keywords=matched,
                explanation=explanation,
            )

    return None


def classify_intent(
    user_message: str,
    *,
    http_client: httpx.Client | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> IntentResult:
    """把用户问题先分到一个粗意图里。"""

    # 项目重点：确定性规则先挡住高风险、高频场景；模型只在规则不确定时兜底。
    rule_result = plan_intent_by_rules(user_message)
    if rule_result and rule_result.confidence >= 0.85:
        return rule_result

    model_result = classify_intent_with_model(
        user_message,
        http_client=http_client,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    if model_result:
        return model_result

    return IntentResult(
        intent="unknown",
        source="rules_fallback",
        confidence=0.3,
        matched_keywords=[],
        explanation="规则没有高置信命中，分类模型也不可用或输出无效，先标记为 unknown。",
    )


def build_answer(intent_result: IntentResult) -> str:
    """根据粗意图生成克制回答。"""

    if intent_result.intent == "complaint":
        return "我已经先把这条消息识别为投诉类问题。当前版本还没有接入人工流转和赔偿处理，不能直接承诺处理结果。"
    if intent_result.intent == "refund_request":
        return "我已经先把这条消息识别为退款或售后类问题。当前版本还没有接入售后规则和订单状态，不能直接判断是否可退。"
    if intent_result.intent == "order_query":
        return "我已经先把这条消息识别为订单或物流查询。当前版本还没有接入订单工具，不能编造物流节点。"
    if intent_result.intent == "promotion_consult":
        return "我已经先把这条消息识别为优惠活动咨询。当前版本还没有接入活动规则，不能承诺具体优惠。"
    if intent_result.intent == "product_consult":
        return "我已经先把这条消息识别为商品咨询。当前版本还没有接入产品知识库，不能编造商品卖点。"
    if intent_result.intent == "general_chat":
        return "你好，我是古河商城客服 Agent。现在我已经能把用户问题先分到一个粗意图里。"
    return "我还不能确定这条消息属于哪类客服问题，只能先标记为 unknown。"


class Step04Agent:
    """这一版的最小意图分拣 Agent。"""

    def __init__(
        self,
        *,
        classifier_http_client: httpx.Client | None = None,
        classifier_api_key: str | None = None,
        classifier_base_url: str | None = None,
        classifier_model_name: str | None = None,
    ) -> None:
        """初始化意图分拣 Agent，并允许测试注入分类模型客户端。"""

        # 仍然只保留最小会话计数，不保存历史对话。
        self._message_count_by_session: dict[str, int] = {}
        self._classifier_http_client = classifier_http_client
        self._classifier_api_key = classifier_api_key
        self._classifier_base_url = classifier_base_url
        self._classifier_model_name = classifier_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理一轮聊天请求，并返回系统可读的第一版 `intent_result`。"""

        # 先记录会话状态，再做粗意图识别，让调试后台看到“这条消息被分到哪里”。
        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent_result = classify_intent(
            request.user_message,
            http_client=self._classifier_http_client,
            api_key=self._classifier_api_key,
            base_url=self._classifier_base_url,
            model=self._classifier_model_name,
        )
        log_guhe_event("INTENT_CLASSIFIED", "结构化意图识别完成", observe=True, intent=intent_result.intent, source=intent_result.source, confidence=intent_result.confidence, matched_keywords=intent_result.matched_keywords)
        # build_answer 提供安全边界，再交给真实模型组织最终客服话术；模型失败时才回退。
        deterministic_answer = build_answer(intent_result)
        log_guhe_event("SAFE_ANSWER_BOUNDARY_READY", "确定性回答边界已生成", fallback_answer=deterministic_answer)
        model_answer = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=deterministic_answer,
            intent_result=intent_result,
        )
        answer = model_answer.answer
        log_guhe_event("GROUNDED_ANSWER_READY", "受边界约束的模型回答已生成", observe=True, used_model=model_answer.used_model, fallback_reason=model_answer.fallback_reason)
        reasoning_summary = [
            "后端接收 ChatRequest，保持 user_message 与 runtime_* 分离。",
            "这一版用规则优先、小模型兜底，把用户问题分成一个粗 intent。",
            "分类结果只用于分拣，不安排售后处理动作。",
            "IntentResult 经过 Pydantic 校验后才进入 ChatResponse。",
        ]
        session_state = {
            "agent_version": "step-04-intent-structured-output",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "model_answer": model_answer.model_dump(),
            "next_gap": "系统知道消息大类，不代表已经知道下一步该怎么处理。",
        }

        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent_result.intent,
            intent_result=intent_result,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
