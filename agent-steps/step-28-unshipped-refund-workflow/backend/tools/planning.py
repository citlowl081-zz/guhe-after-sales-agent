"""Step 28：工具规划和意图识别。这里决定走只读工具、澄清还是高风险边界。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.contracts import *
from tools.runtime_context import *
from integrations.ecommerce_client import *


def extract_order_id(user_message: str) -> str | None:
    """从用户消息中提取订单号。"""
    # Python 的 Unicode `\b` 会把中文和英文字母都视为单词字符，订单号紧贴中文时反而匹配不到。
    # 这里改用 ASCII 显式边界，并只接受古河商城真实使用的 SO 订单号格式，避免误抓 SKU 或订单号尾段。
    match = re.search(r"(?<![A-Za-z0-9_-])SO\d{12,}-[A-Za-z0-9]+(?![A-Za-z0-9_-])", user_message)
    return match.group(0) if match else None

def classify_intent(user_message: str) -> Intent:
    """识别本轮客服意图。"""
    if any(term in user_message for term in ["退货", "寄回", "7天无理由"]):
        return "return_request"
    if any(term in user_message for term in ["退款", "退钱", "取消订单", "直接给我退"]):
        return "refund_request"
    return "unknown"
