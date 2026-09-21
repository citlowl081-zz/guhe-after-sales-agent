"""Step 20：工具规划与澄清判断。这里决定要不要调用工具、缺什么参数、是否需要先问用户。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.contracts import *
from tools.runtime_context import *
from integrations.ecommerce_client import *


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

def pre_tool_clarification(request: ChatRequest, intent: Intent) -> ClarificationRequest | None:
    """执行 pre_tool_clarification 对应的项目逻辑。"""
    if intent in {"order_query", "refund_status_query"} and not extract_order_id(request.user_message):
        return ClarificationRequest(
            clarification_field="order_id",
            message="你要查哪一个订单？请选择订单号，或直接补充订单号。",
            candidates=user_order_candidates(request),
        )
    return None

def plan_tool_action(request: ChatRequest, intent: Intent) -> ToolAction | None:
    """把意图和已知参数转换成结构化工具 Action。"""
    order_id = extract_order_id(request.user_message)
    if intent == "order_query" and order_id:
        return ToolAction(tool_name="get_order_logistics", arguments={"order_id": order_id}, reason="用户已提供订单号，可以查询物流工具。")
    if intent == "refund_status_query" and order_id:
        return ToolAction(tool_name="get_refund_status", arguments={"order_id": order_id}, reason="用户已提供订单号，可以查询退款进度。")
    sku = extract_sku(request.user_message)
    if intent == "product_consult" and sku:
        return ToolAction(tool_name="get_product_inventory", arguments={"sku": sku}, reason="用户询问商品价格或库存，可以查询商品工具。")
    return None
