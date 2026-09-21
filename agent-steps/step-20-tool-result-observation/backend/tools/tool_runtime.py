"""Step 20：工具执行层。只读业务工具在这里校验当前用户身份并读取真实业务事实。"""

from __future__ import annotations

from typing import Any

from api.schemas import *
from guhe_runtime.guhe_logging import observe_tool_execution
from tools.contracts import *
from tools.runtime_context import *
from tools.planning import *
from integrations.ecommerce_client import *


@observe_tool_execution
def execute_tool_action(action: ToolAction, request: ChatRequest) -> ToolResult:
    """执行后端受控的只读工具调用。"""
    if action.tool_name in {"get_order_logistics", "get_refund_status"}:
        target_order_no = action.arguments["order_id"]
        order = find_context_order(request, target_order_no)
        user_matched = True
        if order is None:
            order, user_matched = order_fact_from_ecommerce(target_order_no, request.runtime_user_id)
        if not user_matched:
            return ToolResult(tool_name=action.tool_name, status="error", raw_payload={"error": "forbidden"})
        if order is None:
            return ToolResult(tool_name=action.tool_name, status="error", raw_payload={"error": "order_not_found"})
        if action.tool_name == "get_refund_status":
            return ToolResult(
                tool_name=action.tool_name,
                status="success",
                raw_payload={"order": order},
            )
        logistics = logistics_fact_from_ecommerce(target_order_no, request.runtime_user_id)
        return ToolResult(tool_name=action.tool_name, status="success", raw_payload={"order": order, "logistics": logistics})

    product = product_fact_from_ecommerce(action.arguments["sku"])
    if product is None:
        return ToolResult(tool_name=action.tool_name, status="error", raw_payload={"error": "product_not_found"})
    return ToolResult(tool_name=action.tool_name, status="success", raw_payload={"product": product})
