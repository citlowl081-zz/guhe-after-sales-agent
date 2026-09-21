"""Step 23：工具执行层。这里真正读取业务事实并构造受控工具调用记录。"""

from __future__ import annotations

from typing import Any

from api.schemas import *
from guhe_runtime.guhe_logging import observe_tool_execution
from tools.contracts import *
from tools.runtime_context import *
from integrations.ecommerce_client import *
from observability.observation import latest_logistics_event


def read_business_tool(action: ToolAction, request: ChatRequest) -> dict[str, Any]:
    """执行只读业务工具的核心读取。"""
    if action.tool_name in {"get_order_logistics", "get_refund_status"}:
        target_order_no = str(action.arguments["order_id"])
        order = find_context_order(request, target_order_no)
        if order is None:
            order, allowed = order_fact_from_ecommerce(target_order_no, request.runtime_user_id)
        else:
            allowed = True
        if order is None:
            raise ToolExecutionError("not_found", "没有查到这个订单。")
        if not allowed:
            raise ToolExecutionError("forbidden", "订单不属于当前登录用户。")
        if action.tool_name == "get_refund_status":
            refund_status = str(order.get("refundStatus") or order.get("refund_status") or order.get("status") or "暂无退款申请")
            return {"order": order, "refund_status": refund_status}
        logistics = logistics_fact_from_ecommerce(target_order_no, request.runtime_user_id)
        return {"order": order, "logistics": logistics}

    product = product_fact_from_ecommerce(str(action.arguments["sku"]))
    if product is None:
        raise ToolExecutionError("not_found", "没有查到这个商品。")
    return {"product": product}

@observe_tool_execution
def execute_tool_action(action: ToolAction, request: ChatRequest) -> ToolResult:
    """执行工具并归一化结果。"""
    spec = TOOL_SPECS[action.tool_name]
    attempts = 0
    last_error: ToolExecutionError | None = None
    max_attempts = 2 if spec.read_only else 1
    for _ in range(max_attempts):
        attempts += 1
        try:
            return ToolResult(tool_name=action.tool_name, status="success", raw_payload=read_business_tool(action, request), attempts=attempts)
        except ToolExecutionError as error:
            last_error = error
            if error.category != "timeout" or not spec.read_only:
                break
    assert last_error is not None
    return ToolResult(
        tool_name=action.tool_name,
        status="error",
        raw_payload={"error": str(last_error)},
        attempts=attempts,
        error_category=last_error.category,
    )
