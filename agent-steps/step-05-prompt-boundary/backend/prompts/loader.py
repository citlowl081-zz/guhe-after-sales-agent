"""Prompt 加载层，集中管理项目 Prompt 文档、片段和上下文统计。"""

from __future__ import annotations

from api.schemas import ChatRequest, ContextConflict, IntentResult, PolicyDocument


# 项目重点：这些文档故意混合 current 和 legacy，用来制造“全量注入会冲突”的可观察现场。
FULL_POLICY_DOCUMENTS: list[PolicyDocument] = [
    PolicyDocument(
        doc_id="promo-2026-audio-current",
        title="2026 春季音频节当前活动规则",
        status="current",
        keywords=["降噪耳机", "会员价", "会员券", "满减", "叠加"],
        body=(
            "当前规则：古河商城春季音频节中，降噪耳机会员价不可再叠加会员券、满减券或店铺券。"
            "用户最终可用优惠以结算页实时展示为准，客服不得口头承诺一定可叠加。"
        ),
    ),
    PolicyDocument(
        doc_id="after-sale-2026-current",
        title="2026 售后当前口径",
        status="current",
        keywords=["退款", "退货", "质量问题", "售后", "订单状态"],
        body=(
            "当前口径：退款、退货需要结合订单状态、商品类目和售后规则确认。"
            "客服 Agent 在没有订单和售后事实时，只能说明需要核实，不能承诺马上退款或赔偿到账。"
        ),
    ),
    PolicyDocument(
        doc_id="promo-2024-double11-legacy",
        title="2024 双11耳机活动复盘旧规则",
        status="legacy",
        keywords=["降噪耳机", "会员价", "会员券", "满减", "叠加"],
        body=(
            "历史复盘：2024 双11期间，部分降噪耳机曾允许金卡会员价与一张会员券叠加。"
            "该文档只用于运营复盘，不代表当前活动口径。"
        ),
    ),
    PolicyDocument(
        doc_id="after-sale-2023-legacy",
        title="2023 旧版耳机售后口径",
        status="legacy",
        keywords=["退款", "退货", "耳机", "七天", "质量问题"],
        body=("历史口径：旧版耳机售后曾写过七天内可直接退货。该口径已经被新版售后规则替换，不能单独作为当前处理依据。"),
    ),
    PolicyDocument(
        doc_id="service-boundary",
        title="古河商城客服 Agent 统一边界",
        status="current",
        keywords=["边界", "承诺", "赔偿", "物流", "人工"],
        body=(
            "客服 Agent 必须以古河商城系统确认的事实为准。"
            "涉及优惠、退款、赔偿、发货、签收和人工处理结果时，不得在没有系统依据时直接承诺。"
        ),
    ),
]


# 冲突规则只用于提示“上下文开始打架”，不是完整的政策裁决系统。
CONFLICT_RULES: list[tuple[str, str, str, list[str], str]] = [
    (
        "会员价与会员券是否叠加",
        "promo-2026-audio-current",
        "promo-2024-double11-legacy",
        ["降噪耳机", "会员券", "叠加", "优惠"],
        "当前活动规则说不可叠加，历史复盘旧规则说曾经可叠加。",
    ),
    (
        "耳机售后是否可直接退货",
        "after-sale-2026-current",
        "after-sale-2023-legacy",
        ["退款", "退货", "耳机", "质量问题"],
        "当前售后口径要求结合订单状态和商品规则确认，旧规则容易被误读为直接退货。",
    ),
]


def estimate_tokens(text: str) -> int:
    """粗略估算 token，用来观察上下文变长的趋势，不当作真实计费凭证。"""

    return max(1, len(text) // 2)


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回用户问题中命中的冲突检测关键词。"""

    return [keyword for keyword in keywords if keyword in message]


def build_all_policy_context(documents: list[PolicyDocument]) -> str:
    """把所有文档原样塞进 Prompt。"""

    sections = []
    for document in documents:
        # 项目重点：把状态和标题也放进去，让模型看得到新旧规则差异，但这仍不等于可靠治理。
        sections.append(
            "\n".join(
                [
                    f"文档 ID：{document.doc_id}",
                    f"标题：{document.title}",
                    f"状态：{document.status}",
                    f"正文：{document.body}",
                ]
            )
        )
    return "\n\n---\n\n".join(sections)


def detect_context_conflicts(user_message: str) -> list[ContextConflict]:
    """记录全量 Prompt 里已经能观察到的冲突线索，不负责解决冲突。"""

    message = user_message.strip().lower()
    conflicts: list[ContextConflict] = []
    for topic, newer_doc_id, older_doc_id, keywords, reason in CONFLICT_RULES:
        if first_matched_keywords(message, keywords):
            # 本步只把冲突记录到 session_state，暂不自动删除旧文档或重排上下文。
            conflicts.append(
                ContextConflict(
                    topic=topic,
                    newer_doc_id=newer_doc_id,
                    older_doc_id=older_doc_id,
                    reason=reason,
                )
            )
    return conflicts


def build_full_context_messages(
    request: ChatRequest,
    intent_result: IntentResult,
    documents: list[PolicyDocument],
    conflicts: list[ContextConflict],
) -> list[dict[str, str]]:
    """把系统边界、runtime 事实、粗意图和全量规则文档组装成模型 messages。"""

    # 项目重点：这里故意把所有文档塞进 Prompt，用来暴露长上下文和新旧规则冲突。
    context_text = build_all_policy_context(documents)
    conflict_lines = "\n".join(f"- {conflict.topic}: {conflict.reason}" for conflict in conflicts) or "- 本轮未检测到明显冲突线索。"
    system_message = (
        "你是古河商城公司的客服 Agent。你的首要任务不是讨好用户，而是守住公司事实和高风险边界。\n"
        "\n"
        "事实优先级：\n"
        "1. 古河商城系统传入的 runtime_* 事实优先于用户自称。\n"
        "2. 本步 Prompt 中写明的身份、口径和边界优先于模型常识。\n"
        "3. 没有被系统确认的订单、物流、退款、赔偿和人工处理结果，不能当成事实。\n"
        "4. 规则文档中 current 状态优先于 legacy 和 draft；历史复盘不能当成当前规则。\n"
        "\n"
        "回答边界：\n"
        "- 不得承诺具体优惠、退款、退货、补偿、发货、签收、人工处理结果或到账时间。\n"
        "- 如果当前规则和历史复盘冲突，要说明需要以古河商城当前规则和结算页/系统确认为准。\n"
        "- 当前版本没有订单、物流和售后工具，不得伪装成已经查过或已经提交处理。"
    )
    user_message = (
        "古河商城系统确认的当前用户事实：\n"
        f"- user_id: {request.runtime_user_id}\n"
        f"- nickname: {request.runtime_nickname or '未提供'}\n"
        f"- member_level: {request.runtime_member_level or '未提供'}\n"
        f"- risk_level: {request.runtime_risk_level or '未提供'}\n"
        "\n"
        "Step 04 已经识别出的粗意图：\n"
        f"- intent: {intent_result.intent}\n"
        f"- explanation: {intent_result.explanation}\n"
        "\n"
        "全量规则上下文：\n"
        f"{context_text}\n"
        "\n"
        "本轮上下文冲突观察：\n"
        f"{conflict_lines}\n"
        "\n"
        "用户原话：\n"
        f"{request.user_message}"
    )
    return [{"role": "system", "content": system_message}, {"role": "user", "content": user_message}]
