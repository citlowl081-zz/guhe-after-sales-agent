"""Step 17：工具规划与澄清判断。这里决定要不要调用工具、缺什么参数、是否需要先问用户。"""

from __future__ import annotations

import re

from api.schemas import BusinessFactNeed, ChatRequest, Intent
from integrations.ecommerce_client import product_catalog_from_ecommerce


def classify_intent(user_message: str) -> Intent:
    """用项目规则识别本轮客服意图。"""
    if any(term in user_message for term in ["物流", "快递", "订单", "发货", "到哪"]):
        return "order_query"
    if any(term in user_message for term in ["退款进度", "退款到哪", "退到哪"]):
        return "refund_status_query"
    if any(term in user_message for term in ["库存", "价格", "多少钱", "耳机", "推荐"]):
        return "product_consult"
    return "unknown"

def extract_order_id(user_message: str) -> str | None:
    """从用户消息中提取订单号。

    项目版优先识别 SO 开头的古河订单号，并保留较宽的回退匹配，便于观察
    参数线索如何进入事实查询。生产系统应叠加严格格式、候选订单和澄清机制。
    """
    match = re.search(r"\b(?:SO[A-Za-z0-9_-]{6,}|[A-Za-z0-9][A-Za-z0-9_-]{5,})\b", user_message)
    return match.group(0) if match else None

def extract_sku(user_message: str) -> str | None:
    """把用户提到的商品名或 SKU 映射成商品 SKU。"""
    upper_message = user_message.upper()
    for product in product_catalog_from_ecommerce():
        code = str(product.get("code") or "").upper()
        name = str(product.get("name") or "")
        if code and code in upper_message:
            return code
        if name and name in user_message:
            return code
    return None

def detect_business_fact_need(request: ChatRequest) -> BusinessFactNeed:
    """判断用户问题是否需要实时业务事实。"""
    message = request.user_message
    order_id = extract_order_id(message)
    if any(term in message for term in ["物流", "快递", "到哪", "发货"]):
        return BusinessFactNeed(
            kind="logistics",
            requires_realtime=True,
            order_id=order_id,
            reason="物流状态会随包裹流转变化，不能从稳定知识库推断。",
        )
    if any(term in message for term in ["退款进度", "退款到哪", "退到哪"]):
        return BusinessFactNeed(
            kind="refund_status",
            requires_realtime=True,
            order_id=order_id,
            reason="退款进度属于订单售后实时事实，必须查业务系统。",
        )
    if any(term in message for term in ["库存", "价格", "多少钱"]):
        return BusinessFactNeed(
            kind="product",
            requires_realtime=True,
            sku=extract_sku(message),
            reason="库存和当前价格会随交易变化，不能从知识库缓存回答。",
        )
    if order_id:
        return BusinessFactNeed(
            kind="order",
            requires_realtime=True,
            order_id=order_id,
            reason="订单状态属于当前用户的实时业务事实。",
        )
    return BusinessFactNeed(kind="unknown", requires_realtime=False, reason="本轮没有识别到明确实时业务事实。")
