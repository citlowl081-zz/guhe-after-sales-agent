"""Step 22：工具规划与澄清判断。这里决定要不要调用工具、缺什么参数、是否需要先问用户。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.contracts import *
from tools.runtime_context import *
from integrations.ecommerce_client import *


def classify_intent(user_message: str) -> Intent:
    """用项目规则识别本轮客服意图。"""
    if any(term in user_message for term in ["直接退款", "给我退钱", "马上退款", "取消并退款"]):
        return "refund_request"
    if any(term in user_message for term in ["推荐", "耳机", "库存", "价格", "多少钱", "通勤", "差旅", "降噪"]):
        return "product_consult"
    return "unknown"

def classify_risk(intent: Intent, user_message: str) -> RiskLevel:
    """判断本轮是否触及退款、补偿等高风险动作。"""
    if intent == "refund_request" or any(term in user_message for term in ["赔偿", "补偿", "直接退"]):
        return "high"
    return "low"

def extract_product_sku(user_message: str) -> str | None:
    """根据商品目录和用户场景词定位候选商品 SKU。"""
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

def plan_tool_action(request: ChatRequest, intent: Intent) -> ToolAction | None:
    """把意图和已知参数转换成结构化工具 Action。"""
    sku = extract_product_sku(request.user_message)
    if intent == "product_consult" and sku:
        return ToolAction(
            tool_name="get_product_inventory",
            arguments={"sku": sku},
            reason="商品推荐需要实时库存、当前价格和活动状态。",
        )
    return None
