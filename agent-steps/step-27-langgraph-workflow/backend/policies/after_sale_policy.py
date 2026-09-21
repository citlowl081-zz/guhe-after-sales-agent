"""Step 27：售后政策判断层。高风险动作先查政策和资格，不直接写业务状态。"""

from __future__ import annotations

from api.schemas import *
from config.settings import TODAY
from tools.runtime_context import fulfillment_status, order_no, order_status, payment_status, signed_date
from tools.tool_runtime import logistics_status_from_order, make_tool_call


POLICIES: dict[str, Citation] = {
    "POLICY-REFUND-UNSHIPPED": Citation(
        citation_id="c-refund-unshipped",
        source_title="古河商城公司未发货退款 SOP",
        source_path="knowledge/after_sale_policy.md",
        policy_id="POLICY-REFUND-UNSHIPPED",
        snippet="已支付且未出库、未发货的订单，可以发起退款申请；资金类动作必须进入售后审批。",
    ),
    "POLICY-RETURN-RECEIVED": Citation(
        citation_id="c-return-received",
        source_title="古河商城公司签收后退货 SOP",
        source_path="knowledge/after_sale_policy.md",
        policy_id="POLICY-RETURN-RECEIVED",
        snippet="签收后 7 天内、商品支持退货且原因符合规则时，可以发起退货申请；不得自动批准。",
    ),
}

def retrieve_policy(action_type: Literal["refund", "return", "unknown"]) -> tuple[list[Citation], ToolCallRecord]:
    """读取售后政策依据。"""
    if action_type == "return":
        citations = [POLICIES["POLICY-RETURN-RECEIVED"]]
        query = "签收后退货政策"
    else:
        citations = [POLICIES["POLICY-REFUND-UNSHIPPED"]]
        query = "未发货退款政策"
    return citations, make_tool_call(
        "retrieve_after_sale_policy",
        {"query": query},
        "资格判断必须拿到古河商城公司的售后政策依据。",
        "已检索到售后政策依据。",
        {"policy_ids": [citation.policy_id for citation in citations]},
    )

def assess_refund_boundary(order: dict[str, Any] | None, citations: list[Citation], action_type: Literal["refund", "return", "unknown"]) -> HighRiskAssessment:
    """判断高风险售后动作的资格和边界。"""
    if order is None:
        return HighRiskAssessment(
            action_type=action_type,
            order_id=None,
            eligibility_status="blocked",
            risk_level="high",
            needs_human_approval=True,
            evidence_checklist=["订单归属未通过"],
            policy_basis=citations,
            reasons=["当前没有拿到可信订单事实，不能执行退款、退货或取消。"],
            blocked_write_actions=["create_refund", "approve_refund", "cancel_order", "create_compensation"],
        )

    evidence = ["订单状态", "物流状态", "售后政策依据"]
    reasons: list[str] = []
    eligible = False
    if action_type == "refund":
        eligible = (
            fulfillment_status(order) == "PENDING_SHIPMENT"
            and payment_status(order) == "PAID"
            and logistics_status_from_order(order) == "NOT_SHIPPED"
        )
        if eligible:
            reasons.append("订单已支付、未出库、未发货，只能判断为可发起退款申请。")
        else:
            reasons.append("订单已经出库、发货或状态不满足未发货退款条件，不能按一句话直接退款。")
    elif action_type == "return":
        eligible = fulfillment_status(order) == "DELIVERED" and logistics_status_from_order(order) == "SIGNED" and bool(order.get("returnable", True))
        if eligible:
            reasons.append("订单已签收且商品支持退货，但仍只能发起申请，不能自动批准。")
        else:
            reasons.append("订单或商品状态不满足签收后退货的基础条件。")
    else:
        reasons.append("用户没有说清楚是退款还是退货，需要先澄清售后类型。")

    return HighRiskAssessment(
        action_type=action_type,
        order_id=order_no(order),
        eligibility_status="eligible_for_application" if eligible else "not_eligible",
        risk_level="high",
        needs_human_approval=True,
        evidence_checklist=evidence,
        policy_basis=citations,
        reasons=reasons,
        blocked_write_actions=["create_refund", "approve_refund", "cancel_order", "create_compensation"],
    )
