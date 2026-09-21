"""Step 24：工具规划和意图识别。这里决定走只读工具、澄清还是高风险边界。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.contracts import *
from tools.runtime_context import *
from integrations.ecommerce_client import *


def classify_intent(user_message: str) -> Intent:
    """识别本轮客服意图。"""
    if any(term in user_message for term in ["直接退款", "给我退钱", "马上退款", "取消并退款"]):
        return "refund_request"
    if any(term in user_message for term in ["退款进度", "退款到哪", "退到哪"]):
        return "refund_status_query"
    if any(term in user_message for term in ["物流", "快递", "订单", "发货", "到哪"]):
        return "order_query"
    if any(term in user_message for term in ["库存", "价格", "多少钱", "耳机", "推荐"]):
        return "product_consult"
    return "unknown"

def classify_risk(intent: Intent, user_message: str) -> RiskLevel:
    """判断本轮风险等级。"""
    if intent == "refund_request" or any(term in user_message for term in ["赔偿", "补偿", "直接退"]):
        return "high"
    if intent == "refund_status_query":
        return "medium"
    return "low"

def extract_order_id(user_message: str) -> str | None:
    """从用户消息中提取订单号。"""
    match = re.search(r"\b(?:SO[A-Za-z0-9_-]{6,}|[A-Za-z0-9][A-Za-z0-9_-]{5,})\b", user_message)
    return match.group(0) if match else None

def extract_sku(user_message: str) -> str | None:
    """从用户消息中提取商品 SKU。"""
    upper_message = user_message.upper()
    for product in product_catalog_from_ecommerce():
        code = str(product.get("code") or "").upper()
        name = str(product.get("name") or "")
        tags = str(product.get("scenarioTags") or "")
        description = str(product.get("description") or "")
        searchable = f"{name} {tags} {description}"
        if code and code in upper_message:
            return code
        if any(term and term in searchable for term in ["降噪", "耳机", "通勤", "差旅", "音箱"] if term in user_message):
            return code
    return None

def user_order_candidates(request: ChatRequest) -> list[ClarificationCandidate]:
    """从可信运行时上下文提取当前用户可见订单，避免模型伪造归属。"""
    return [
        ClarificationCandidate(value=order_no(order), label=f"{order_no(order)}｜{order_status(order) or '状态待查'}", hint="、".join(item_summary(order)) or "当前用户真实订单")
        for order in current_user_orders(request)
        if order_no(order)
    ]

def pre_tool_clarification(request: ChatRequest, intent: Intent) -> ClarificationRequest | None:
    """缺少关键参数时先生成澄清请求。"""
    if intent in {"order_query", "refund_status_query"} and not extract_order_id(request.user_message):
        return ClarificationRequest(
            clarification_field="order_id",
            message="你要查哪一个订单？请选择订单号，或直接补充订单号。",
            candidates=user_order_candidates(request),
        )
    return None

def plan_tool_action(request: ChatRequest, intent: Intent, tool_specs: dict[str, ToolSpec]) -> ToolAction | None:
    """把意图转换成结构化工具 Action。"""
    order_id = extract_order_id(request.user_message)
    if intent == "order_query" and order_id and "get_order_logistics" in tool_specs:
        return ToolAction(tool_name="get_order_logistics", arguments={"order_id": order_id}, reason="MCP 目录声明物流工具可用，Tool Use 选择它查询订单物流。")
    if intent == "refund_status_query" and order_id and "get_refund_status" in tool_specs:
        return ToolAction(tool_name="get_refund_status", arguments={"order_id": order_id}, reason="MCP 目录声明退款进度是只读工具，Tool Use 可以查询状态。")
    sku = extract_sku(request.user_message)
    if intent == "product_consult" and sku and "get_product_inventory" in tool_specs:
        return ToolAction(tool_name="get_product_inventory", arguments={"sku": sku}, reason="MCP 目录声明商品库存价格工具可用，Tool Use 选择它查询实时事实。")
    return None
