"""Step 18：意图识别与参数提取。LangChain tool call 之前先准备可识别的业务线索。"""

from __future__ import annotations

import re

from api.schemas import Intent
from integrations.ecommerce_client import product_catalog_from_ecommerce


def classify_intent(user_message: str) -> Intent:
    """用项目规则识别本轮客服意图。"""
    if any(term in user_message for term in ["退款进度", "退款到哪", "退到哪"]):
        return "refund_status_query"
    if any(term in user_message for term in ["物流", "快递", "订单", "发货", "到哪"]):
        return "order_query"
    if any(term in user_message for term in ["库存", "价格", "多少钱", "耳机", "推荐"]):
        return "product_consult"
    return "unknown"

def extract_order_id(user_message: str) -> str | None:
    """从用户消息中提取订单号。"""
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
