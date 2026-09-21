"""Step 26：工具规划和意图识别。这里决定走只读工具、澄清还是高风险边界。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.contracts import *
from tools.runtime_context import *
from integrations.ecommerce_client import *


def extract_order_id(user_message: str) -> str | None:
    """从用户消息中提取订单号。"""
    match = re.search(r"\b(?:SO[A-Za-z0-9_-]{6,}|[A-Za-z0-9][A-Za-z0-9_-]{5,})\b", user_message)
    return match.group(0) if match else None

def classify_intent(user_message: str) -> Intent:
    """识别本轮客服意图。"""
    if any(term in user_message for term in ["退货", "寄回", "7天无理由"]):
        return "return_request"
    if any(term in user_message for term in ["退款", "退钱", "取消订单", "直接给我退"]):
        return "refund_request"
    return "unknown"
