"""Step 19：工具执行层。只读业务工具在这里校验当前用户身份并读取真实业务事实。"""

from __future__ import annotations

from typing import Any

from api.schemas import *
from guhe_runtime.guhe_logging import observe_tool_execution
from tools.contracts import *
from tools.runtime_context import *
from tools.runtime_context import _item_summary, _order_created_month, _order_no, _order_status
from tools.planning import *
from integrations.ecommerce_client import *


def search_current_user_orders_in_business_backend(
    current_user_id: str,
    month: int,
) -> list[dict[str, Any]]:
    """展示真实业务库查询能力，但 Step 19 不会把它注册给模型或默认执行链。"""
    # 已发布项目仍以 Runtime Context 讲清澄清边界；这个函数仅展示后续可替换为实时查询的能力。
    return current_user_orders_from_ecommerce(current_user_id, month=month)


def summarize_order_logistics(order: dict[str, Any], logistics: dict[str, Any] | None = None) -> str:
    """执行 summarize_order_logistics 对应的项目逻辑。"""
    order_no = _order_no(order)
    payment_status = str(order.get("paymentStatus") or "").strip()
    fulfillment_status = str(order.get("fulfillmentStatus") or "").strip()
    status = _order_status(order)
    logistics_no = str(order.get("logisticsNo") or "").strip()
    if payment_status == "UNPAID":
        return f"{order_no} 当前还未支付，订单尚未进入发货流程。"
    if fulfillment_status in {"UNSHIPPED", "PENDING_SHIPMENT"} or status in {"PENDING_PAYMENT", "PAID_PENDING_SHIPMENT", "PENDING_SHIPMENT"}:
        return f"{order_no} 当前状态是 {status or fulfillment_status}，还没有发货，因此暂时没有物流轨迹。"
    if logistics:
        latest = logistics.get("latestUpdate") or "暂无最新轨迹"
        delivery = logistics.get("estimatedDelivery") or logistics.get("deliveredAt") or ""
        return f"{order_no} 物流状态：{logistics.get('status')}，承运方：{logistics.get('company')}，最新轨迹：{latest}。{delivery}"
    if logistics_no:
        return f"{order_no} 已生成物流单 {logistics_no}，当前状态是 {status or fulfillment_status}。"
    return f"{order_no} 当前状态是 {status or fulfillment_status}，暂未查到独立物流轨迹。"

@observe_tool_execution
def execute_tool_action(action: ToolAction, request: ChatRequest) -> ToolObservation:
    """执行后端受控的只读工具调用。"""
    missing = [field for field in TOOL_SPECS[action.tool_name].required if not action.arguments.get(field)]
    if missing:
        return ToolObservation(tool_name=action.tool_name, status="error", summary=f"工具参数缺失：{', '.join(missing)}。")
    if action.tool_name == "search_current_user_orders":
        month = action.arguments["month"]
        # Step 19 只查询网关已按当前用户注入的候选，保持 Tool 与 Runtime Context 的项目边界一致。
        orders = [
            order for order in current_user_orders(request) if _order_created_month(order) == month
        ]
        context_truncated = current_user_orders_truncated(request)
        summary = f"按 {month} 月查询到 {len(orders)} 个当前用户订单候选。"
        if context_truncated:
            summary += " 当前订单上下文已截断，结果只代表已加载窗口，不能据此确认其他订单不存在。"
        return ToolObservation(
            tool_name=action.tool_name,
            status="success",
            summary=summary,
            data={
                "month": month,
                "context_truncated": context_truncated,
                "candidate_orders": [
                    {
                        "order_id": _order_no(order),
                        "status": _order_status(order),
                        "items": _item_summary(order),
                    }
                    for order in orders
                ],
            },
        )
    if action.tool_name in {"get_order_logistics", "get_refund_status"}:
        order_id = action.arguments["order_id"]
        order = find_context_order(request, order_id) or order_fact_from_ecommerce(order_id, request.runtime_user_id)
        if order is None:
            return ToolObservation(tool_name=action.tool_name, status="error", summary="没有查到这个订单。")
        if action.tool_name == "get_refund_status":
            status = _order_status(order)
            return ToolObservation(
                tool_name=action.tool_name,
                status="success",
                summary=f"{_order_no(order)} 当前订单状态：{status or '待查'}。Step 19 只做只读状态查询，不创建退款申请。",
                data={"order_id": _order_no(order), "order": order},
            )
        logistics = logistics_fact_from_ecommerce(order_id, request.runtime_user_id)
        return ToolObservation(
            tool_name=action.tool_name,
            status="success",
            summary=summarize_order_logistics(order, logistics),
            data={"order": order, "logistics": logistics},
        )
    product = product_fact_from_ecommerce(action.arguments["sku"])
    if product is None:
        return ToolObservation(tool_name=action.tool_name, status="error", summary="没有查到这个商品。")
    return ToolObservation(
        tool_name=action.tool_name,
        status="success",
        summary=f"{product.get('name')} 当前库存 {product.get('stock')} 件，当前价 {product.get('price')} 元。",
        data={"product": product},
    )
