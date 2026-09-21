"""Step 13 查询改写逻辑。

用户原话用于对话和审计，改写后的 rewritten_query 只用于检索。
"""

from __future__ import annotations

from api.schemas import ChatRequest, Intent, QueryRewrite
from config.settings import NORMALIZATION_RULES


def normalize_query(text: str) -> str:
    """把用户口语归一到知识库更常见的检索词。"""

    normalized = text.lower()
    for source, target, _reason in NORMALIZATION_RULES:
        normalized = normalized.replace(source, target)
    return normalized


def describe_normalization(original_query: str) -> list[str]:
    """返回本轮归一化命中的原因说明。"""

    query = original_query.lower()
    return [reason for source, _target, reason in NORMALIZATION_RULES if source in query]


def add_rewrite_terms(target: list[str], terms: list[str]) -> None:
    """向改写词列表中追加不重复的检索补词。"""

    for term in terms:
        if term not in target:
            target.append(term)


def build_rewrite_reason(normalization_reasons: list[str], rewrite_reasons: list[str]) -> str:
    """把归一化和补词原因合并成调试后台可读说明。"""

    reasons = [*normalization_reasons, *rewrite_reasons]
    if reasons:
        return "；".join(reasons) + "。"
    return "未命中归一化或补词规则，保留原问题直接检索，后续只对候选片段做 reranker 重排。"


def rewrite_retrieval_query(request: ChatRequest, intent: Intent) -> QueryRewrite:
    """根据粗意图、口语表达和运行时上下文生成检索问题。"""

    normalized = normalize_query(request.user_message)
    added_terms: list[str] = []
    rewrite_reasons: list[str] = []

    if intent == "promotion_consult" and "耳机" in normalized:
        # Step 13 的关键点：用户原话不改，检索问题单独改写。
        add_rewrite_terms(added_terms, ["当前", "2026", "春季音频节"])
        rewrite_reasons.append("促销咨询提到耳机，补齐当前活动时间和活动名")
        add_rewrite_terms(added_terms, ["降噪耳机"])
        rewrite_reasons.append("补齐知识库里的具体商品类目“降噪耳机”")
        add_rewrite_terms(added_terms, ["会员价", "优惠券", "叠加", "结算页"])
        rewrite_reasons.append("补齐会员价、优惠券叠加和结算页边界，给 reranker 更多排序信号")
        if request.runtime_member_level == "gold":
            add_rewrite_terms(added_terms, ["金卡"])
            rewrite_reasons.append("运行时上下文显示当前用户是金卡会员，补充会员等级用于检索和重排")
    elif intent == "refund_request":
        add_rewrite_terms(added_terms, ["售后规则", "签收时间", "退货条件", "凭证"])
        rewrite_reasons.append("售后意图需要补齐签收时间、退货条件和凭证要求这些规则检索词")

    rewritten_query = " ".join(part for part in [normalized, *added_terms] if part).strip() or request.user_message
    return QueryRewrite(
        original_query=request.user_message,
        rewritten_query=rewritten_query,
        applied=rewritten_query != request.user_message,
        added_terms=added_terms,
        reason=build_rewrite_reason(describe_normalization(request.user_message), rewrite_reasons),
    )
