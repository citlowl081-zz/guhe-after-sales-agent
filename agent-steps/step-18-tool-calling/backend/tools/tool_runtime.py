"""Step 18：工具执行层。只读业务工具在这里校验当前用户身份并读取真实业务事实。"""

from __future__ import annotations

from typing import Any

from api.schemas import ChatRequest, ToolAction, ToolObservation
from guhe_runtime.guhe_logging import observe_tool_execution
from integrations.ecommerce_client import logistics_fact_from_ecommerce, order_fact_from_ecommerce, product_fact_from_ecommerce
from tools.contracts import TOOL_SPECS
from tools.runtime_context import find_context_order, order_no, order_status


def validate_tool_action(action: ToolAction) -> ToolObservation | None:
    """校验模型给出的工具名和必填参数。"""
    spec = TOOL_SPECS.get(action.tool_name)
    if spec is None:
        return ToolObservation(tool_name=action.tool_name, status="error", summary="工具不存在。")
    missing = [field for field in spec.required if not action.arguments.get(field)]
    if missing:
        return ToolObservation(
            tool_name=action.tool_name,
            status="error",
            summary=f"工具参数缺失：{', '.join(missing)}。",
            data={"missing": missing, "parameters_schema": spec.parameters_schema},
        )
    return None

def summarize_order_logistics(order: dict[str, Any], logistics: dict[str, Any] | None) -> str:
    """把订单事实和物流事实整理成可直接回答用户的摘要。"""
    target_order_no = order_no(order)
    payment_status = str(order.get("paymentStatus") or "").strip()
    fulfillment_status = str(order.get("fulfillmentStatus") or "").strip()
    status = order_status(order)
    if payment_status == "UNPAID":
        return f"{target_order_no} 当前还未支付，订单尚未进入发货流程。"
    if fulfillment_status in {"UNSHIPPED", "PENDING_SHIPMENT"} or status in {"PENDING_PAYMENT", "PAID_PENDING_SHIPMENT", "PENDING_SHIPMENT"}:
        return f"{target_order_no} 当前状态是 {status or fulfillment_status}，还没有发货，因此暂时没有物流轨迹。"
    if logistics:
        latest = logistics.get("latestUpdate") or "暂无最新轨迹"
        return f"{target_order_no} 物流状态：{logistics.get('status')}，承运方：{logistics.get('company')}，最新轨迹：{latest}。"
    return f"{target_order_no} 当前状态是 {status or fulfillment_status}，暂未查到独立物流轨迹。"

@observe_tool_execution
def execute_tool_action(action: ToolAction, request: ChatRequest) -> ToolObservation:
    """执行后端受控的只读工具调用。"""
    validation_error = validate_tool_action(action)
    if validation_error:
        return validation_error

    if action.tool_name in {"get_order_logistics", "get_refund_status"}:
        order_id = action.arguments["order_id"]
        order = find_context_order(request, order_id)
        user_matched = True
        if order is None:
            order, user_matched = order_fact_from_ecommerce(order_id, request.runtime_user_id)
        if not user_matched:
            return ToolObservation(tool_name=action.tool_name, status="error", summary="订单不属于当前登录用户。")
        if order is None:
            return ToolObservation(tool_name=action.tool_name, status="error", summary="没有查到这个订单。")
        if action.tool_name == "get_refund_status":
            return ToolObservation(
                tool_name=action.tool_name,
                status="success",
                summary=f"{order_no(order)} 当前订单状态：{order_status(order) or '待查'}。Step 18 只做只读状态查询，不创建退款申请。",
                data={"order_id": order_no(order), "order": order},
            )
        logistics = logistics_fact_from_ecommerce(order_id, request.runtime_user_id)
        return ToolObservation(
            tool_name=action.tool_name,
            status="success",
            summary=summarize_order_logistics(order, logistics),
            data={"order": order, "logistics": logistics},
        )

    if action.tool_name == "get_product_inventory":
        product = product_fact_from_ecommerce(action.arguments["sku"])
        if product is None:
            return ToolObservation(tool_name=action.tool_name, status="error", summary="没有查到这个商品。")
        return ToolObservation(
            tool_name=action.tool_name,
            status="success",
            summary=f"{product.get('name')} 当前库存 {product.get('stock')} 件，当前价 {product.get('price')} 元。",
            data={"product": product},
        )

    return ToolObservation(tool_name=action.tool_name, status="error", summary="工具未处理。")
