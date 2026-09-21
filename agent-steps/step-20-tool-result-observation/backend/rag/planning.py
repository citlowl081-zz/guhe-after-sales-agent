"""沿用前序稳定知识 RAG 的 pre-retrieval 计划。"""

from __future__ import annotations

import re

from api.schemas import ChatRequest, Intent, KnowledgeChunk, KnowledgeTopic, RetrievalPlan, RetrievalScene


def normalize_query(text: str) -> str:
    """把口语表达归一成更容易检索的词。"""

    return (
        text.lower()
        .replace("那个", "")
        .replace("这个", "")
        .replace("耳麦", "耳机")
        .replace("叠券", "叠加 优惠券")
        .replace("会员券", "优惠券")
        .replace("促销", "活动")
        .replace("少一根", "少了 配件")
        .replace("线少了", "配件 少了")
        .replace("盒子", "包装盒")
        .replace("压了", "压坏")
    )


def classify_intent(user_message: str) -> Intent:
    """识别粗意图，让检索计划知道优先场景。"""

    message = normalize_query(user_message)
    if any(word in message for word in ["投诉", "举报", "赔偿", "曝光", "315"]):
        return "complaint"
    if any(word in message for word in ["退款", "退货", "取消订单", "坏了", "无法开机", "质量问题", "无理由", "配件", "赠品", "包装盒"]):
        return "refund_request"
    if any(word in message for word in ["物流", "快递", "发货", "到哪", "运单", "订单"]):
        return "order_query"
    if any(word in message for word in ["优惠", "活动", "会员价", "券", "满减", "折扣"]):
        return "promotion_consult"
    if any(word in message for word in ["耳机", "充电器", "音箱", "推荐", "哪个好"]):
        return "product_consult"
    return "unknown"


def detect_scene(query: str, intent: Intent) -> RetrievalScene:
    """根据问题和意图判断检索场景。"""

    text = normalize_query(query)
    if any(term in text for term in ["配件", "赠品", "包装盒", "退货", "退款", "无理由", "质量问题", "售后"]):
        return "after_sale"
    if any(term in text for term in ["物流", "快递", "发货", "运单", "订单"]):
        return "shipping"
    if any(term in text for term in ["优惠", "活动", "会员价", "优惠券", "满减", "叠加"]):
        return "promotion"
    if intent == "product_consult" or "耳机" in text:
        return "product"
    return "unknown"


def keyword_terms_for_scene(query: str, scene: RetrievalScene) -> list[str]:
    """根据场景补齐关键词召回使用的术语。"""

    text = normalize_query(query)
    terms: list[str] = []
    for term in ["当前", "2026", "春季", "音频节", "降噪", "耳机", "会员价", "优惠券", "叠加", "结算页"]:
        if term in text or scene == "promotion":
            terms.append(term)
    for term in ["签收", "7天", "八天", "无理由", "退货", "配件", "赠品", "少了", "缺失", "包装盒", "压坏", "凭证", "售后"]:
        if term in text or scene == "after_sale":
            terms.append(term)
    for term in ["发货", "物流", "快递", "现货", "预售", "48小时"]:
        if term in text or scene == "shipping":
            terms.append(term)
    return list(dict.fromkeys(terms))


def is_realtime_business_query(query: str) -> bool:
    """识别必须交给业务工具的实时问题。"""

    text = normalize_query(query)
    realtime_terms = ["我的订单", "订单到哪", "快递到哪", "物流到哪", "退款进度", "库存还有", "有没有库存", "发货了吗"]
    if any(term in text for term in realtime_terms):
        return True
    # 订单号是业务实体证据；带订单号询问到哪、物流、发货或退款状态，也必须走实时事实层。
    has_order_id = re.search(r"\bso\d{14,}-a\d+\b", text) is not None
    asks_current_status = any(term in text for term in ["到哪", "物流", "快递", "发货", "退款状态", "退款进度"])
    return has_order_id and asks_current_status


def build_plan_reason(scene: RetrievalScene, allowed_topics: list[KnowledgeTopic], added_terms: list[str]) -> str:
    """生成检索计划的可读原因。"""

    topic_text = "、".join(allowed_topics)
    scene_labels: dict[RetrievalScene, str] = {
        "promotion": "促销",
        "after_sale": "售后",
        "shipping": "物流",
        "product": "商品",
        "unknown": "未知",
    }
    if scene == "unknown":
        return f"未识别到需要收窄的知识场景，保留全主题候选（{topic_text}），不额外补词。"

    reason = f"识别为{scene_labels[scene]}知识场景，限制到 {topic_text} 主题"
    if added_terms:
        reason += f"，并补齐{'、'.join(added_terms)}关键词。"
    else:
        reason += "，不额外补词；关键词召回使用当前检索计划生成的 keyword_terms。"
    return reason


def pre_retrieval_plan(request: ChatRequest, intent: Intent) -> RetrievalPlan:
    """在真正检索前决定主题范围和关键词补充。"""

    scene = detect_scene(request.user_message, intent)
    allowed_topics_by_scene: dict[RetrievalScene, list[KnowledgeTopic]] = {
        "promotion": ["promotion"],
        "after_sale": ["after_sale"],
        "shipping": ["shipping"],
        "product": ["product", "promotion"],
        "unknown": ["promotion", "product", "after_sale", "shipping", "faq"],
    }
    normalized = normalize_query(request.user_message)
    added: list[str] = []
    if scene == "promotion":
        added = ["当前", "2026", "春季音频节", "会员价", "优惠券", "叠加", "结算页"]
    elif scene == "after_sale":
        added = ["售后规则", "配件", "赠品", "包装盒", "凭证"]
    rewritten = " ".join(part for part in [normalized, *added] if part).strip()
    allowed_topics = allowed_topics_by_scene[scene]
    return RetrievalPlan(
        original_query=request.user_message,
        rewritten_query=rewritten or request.user_message,
        scene=scene,
        allowed_topics=allowed_topics,
        keyword_terms=keyword_terms_for_scene(rewritten, scene),
        reason=build_plan_reason(scene, allowed_topics, added),
    )


def topic_allowed(chunk: KnowledgeChunk, plan: RetrievalPlan) -> bool:
    """判断知识片段是否属于本轮允许主题。"""

    return chunk.topic in plan.allowed_topics
