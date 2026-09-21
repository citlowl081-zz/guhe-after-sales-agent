"""Step 22：工具执行层。只读业务工具在这里校验当前用户身份并读取真实业务事实。"""

from __future__ import annotations

from typing import Any

from api.schemas import *
from guhe_runtime.guhe_logging import observe_tool_execution
from tools.contracts import *
from tools.runtime_context import *
from tools.planning import *
from integrations.ecommerce_client import *


@observe_tool_execution
def execute_product_tool(action: ToolAction) -> Observation:
    """执行 execute_product_tool 对应的项目逻辑。"""
    product = product_fact_from_ecommerce(str(action.arguments["sku"]))
    if product is None:
        return Observation(tool_name=action.tool_name, status="error", summary="没有查到这个商品。", facts={}, next_action="fallback_answer")
    facts = normalize_product_fact(product)
    price_text = f"{facts['current_price']} 元"
    if facts.get("promotion_price"):
        price_text += f"，活动价 {facts['promotion_price']} 元"
    return Observation(
        tool_name=action.tool_name,
        status="success",
        summary=f"{facts['name']} 当前库存 {facts['inventory']} 件，标价 {price_text}，活动：{facts['activity']}。",
        facts=facts,
        next_action="answer_user",
    )
