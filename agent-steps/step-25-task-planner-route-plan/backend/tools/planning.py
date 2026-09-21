"""Step 25：工具规划和意图识别。这里决定走只读工具、澄清还是高风险边界。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.runtime_context import *
from integrations.ecommerce_client import *


def product_matches_user_message(product: dict[str, Any], user_message: str) -> bool:
    """判断商品是否匹配用户问题。"""
    code = str(product.get("code") or "").upper()
    searchable = " ".join(
        str(product.get(key) or "")
        for key in ("name", "category", "description", "highlights", "scenarioTags")
    )
    return code in user_message.upper() or any(term in searchable for term in ["降噪", "耳机", "通勤", "差旅", "音箱", "充电器"] if term in user_message)

def select_product_candidate(user_message: str) -> dict[str, Any] | None:
    """从商品目录里选择本轮候选商品。"""
    for product in product_catalog_from_ecommerce():
        if product_matches_user_message(product, user_message):
            return product
    return None
