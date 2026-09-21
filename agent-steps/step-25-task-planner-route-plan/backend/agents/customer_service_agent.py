"""Step 25：Agent 编排层。每步只在这里串联已经长出的模块。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from typing import Any, Literal

from api.schemas import *
from config.settings import *
from tools.runtime_context import *
from integrations.ecommerce_client import *
from tools.planning import *
from observability.observation import *
from models.answer_client import compose_grounded_answer
from tools.catalog import ToolCatalog
from models.task_planner_client import TaskPlannerModelClient
from planner.task_planner import TaskPlanner
from rag.knowledge import *


TOOL_CATALOG = ToolCatalog()

TASK_PLANNER = TaskPlanner(TOOL_CATALOG, TaskPlannerModelClient())

def build_clarification(request: ChatRequest, route_plan: RoutePlan) -> ClarificationRequest | None:
    """根据 RoutePlan 构造澄清请求。"""
    if route_plan.intent != "order_logistics":
        return None
    if TaskPlanner.ORDER_ID_PATTERN.search(request.user_message):
        return None
    candidates = [
        ClarificationCandidate(value=order_no(order), label=f"{order_no(order)}｜{order_status(order) or '状态待查'}", hint="、".join(item_summary(order)) or "当前用户真实订单")
        for order in current_user_orders(request)
        if order_no(order)
    ]
    return ClarificationRequest(
        clarification_field="order_id",
        message="你要查哪一个订单的物流？",
        candidates=candidates,
    )

def execute_allowed_light_path(request: ChatRequest, route_plan: RoutePlan) -> list[ToolCallRecord]:
    """只执行 RoutePlan 允许的低风险路径。"""
    if route_plan.requires_workflow:
        return []

    tool_calls: list[ToolCallRecord] = []
    order_match = TaskPlanner.ORDER_ID_PATTERN.search(request.user_message)
    if "get_order_logistics" in route_plan.required_tools and order_match:
        target_order_no = order_match.group(0)
        order = find_context_order(request, target_order_no)
        if order is None:
            order, allowed = order_fact_from_ecommerce(target_order_no, request.runtime_user_id)
        else:
            allowed = True
        if order and allowed:
            logistics = logistics_fact_from_ecommerce(target_order_no, request.runtime_user_id)
            logistics_status = str((logistics or {}).get("status") or "暂无物流状态")
            latest_event = latest_logistics_event(logistics)
            summary = f"{target_order_no} 物流状态：{logistics_status}，最新节点：{latest_event}。"
            facts = {"order_id": target_order_no, "logistics_status": logistics_status, "latest_event": latest_event}
        else:
            summary = "没有找到当前用户可查询的这个订单。"
            facts = {"order_id": target_order_no, "visible_to_current_user": False}
        tool_calls.append(
            ToolCallRecord(
                action=ToolAction(
                    tool_name="get_order_logistics",
                    arguments={"order_id": target_order_no},
                    reason="RoutePlan 把本轮收窄到物流只读工具。",
                ),
                observation=Observation(
                    tool_name="get_order_logistics",
                    status="success",
                    summary=summary,
                    facts=facts,
                    next_action="answer_user",
                ),
            )
        )

    if "search_products" in route_plan.required_tools:
        product = select_product_candidate(request.user_message)
        sku = str((product or {}).get("code") or "")
        name = str((product or {}).get("name") or "未找到明确商品候选")
        tool_calls.append(
            ToolCallRecord(
                action=ToolAction(
                    tool_name="search_products",
                    arguments={"keyword": request.user_message},
                    reason="RoutePlan 识别商品咨询，需要先找到商品候选。",
                ),
                observation=Observation(
                    tool_name="search_products",
                    status="success",
                    summary=f"找到商品候选：{name}。" if product else "没有找到明确商品候选。",
                    facts={"sku": sku, "name": name},
                    next_action="answer_user",
                ),
            )
        )

    if "get_product_inventory" in route_plan.required_tools:
        product = select_product_candidate(request.user_message)
        sku = str((product or {}).get("code") or "")
        inventory = (product or {}).get("stock")
        current_price = (product or {}).get("price")
        promotion = (product or {}).get("promotion") if isinstance((product or {}).get("promotion"), dict) else {}
        promotion_price = promotion.get("promotionPrice")
        product_name = str((product or {}).get("name") or "商品")
        summary = (
            f"{product_name} 当前库存 {inventory} 件，标价 {current_price} 元"
            + (f"，活动价 {promotion_price} 元。" if promotion_price else "。")
            if product
            else "没有拿到可靠商品库存价格事实。"
        )
        tool_calls.append(
            ToolCallRecord(
                action=ToolAction(
                    tool_name="get_product_inventory",
                    arguments={"sku": sku},
                    reason="库存和价格属于实时业务事实。",
                ),
                observation=Observation(
                    tool_name="get_product_inventory",
                    status="success",
                    summary=summary,
                    facts={"sku": sku, "inventory": inventory, "current_price": current_price, "promotion_price": promotion_price},
                    next_action="answer_user",
                ),
            )
        )

    if "get_current_user_coupons" in route_plan.required_tools:
        product_category = next(
            (category for category in ("耳机", "音箱", "充电器") if category in request.user_message),
            None,
        )
        coupons, available = user_coupons_from_ecommerce(request.runtime_user_id, product_category)
        public_coupons = [
            {
                "coupon_code": coupon.get("couponCode"),
                "coupon_name": coupon.get("couponName"),
                "discount_amount": coupon.get("discountAmount"),
                "threshold_amount": coupon.get("thresholdAmount"),
                "applicable_categories": coupon.get("applicableCategories"),
            }
            for coupon in coupons[:3]
        ]
        if available:
            summary = f"当前用户共有 {len(coupons)} 张可用优惠券，是否适用于商品仍以券规则和结算页为准。"
            status: ToolStatus = "success"
        else:
            summary = "优惠券服务暂时不可用，本轮不能编造当前用户优惠权益。"
            status = "skipped"
        tool_calls.append(
            ToolCallRecord(
                action=ToolAction(
                    tool_name="get_current_user_coupons",
                    arguments={"product_category": product_category} if product_category else {},
                    reason="当前用户优惠券属于实时权益，必须使用可信 runtime_user_id 查询。",
                ),
                observation=Observation(
                    tool_name="get_current_user_coupons",
                    status=status,
                    summary=summary,
                    facts={"coupon_count": len(coupons), "coupons": public_coupons},
                    next_action="answer_user",
                ),
            )
        )

    return tool_calls

def build_answer(route_plan: RoutePlan, clarification: ClarificationRequest | None, tool_calls: list[ToolCallRecord], citations: list[Citation]) -> tuple[str, NextAction]:
    """组织最终用户回答和下一步动作。"""
    if clarification:
        return clarification.message, "ask_clarification"
    if route_plan.requires_workflow:
        return (
            "这属于高风险售后请求。本步只把它路由到后续受控售后路径，不执行退款、取消订单或补偿。",
            "route_to_controlled_workflow",
        )
    if tool_calls and route_plan.intent == "order_logistics":
        return tool_calls[0].observation.summary, "answer_user"
    if route_plan.needs_rag and route_plan.needs_business_tools:
        return "这类问题需要同时看商品知识和实时业务事实：我会先查商品候选、库存/优惠，再用古河商城规则补充边界。", "answer_user"
    if citations:
        return f"这个问题先走知识库路径，当前命中 {citations[0].source_title}，回答时要带依据而不是凭模型口感。", "answer_user"
    return "这个问题没有命中业务工具或知识库路径，可以按普通客服对话收口。", "answer_user"

def reasoning_summary(route_plan: RoutePlan, trace: PlannerTrace) -> list[str]:
    """生成调试可见的规划摘要。"""
    return [
        f"TaskPlanner 输出 intent={route_plan.intent}，source={route_plan.source}，confidence={route_plan.confidence:.2f}。",
        f"本轮候选工具收窄为：{', '.join(tool.name for tool in trace.candidate_tools) or '无'}。",
        trace.public_reason,
    ]

def health() -> dict[str, str]:
    """返回当前步程健康状态。"""
    return {"status": "ok", "step": "step-25-task-planner-route-plan"}

def capabilities() -> dict[str, Any]:
    """返回当前步程能力开关。"""
    return json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))

@observe_chat
async def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给 Agent 编排。"""
    runtime_context = request.runtime_context or {
        "runtime_user_id": request.runtime_user_id,
        "member_level": request.runtime_member_level,
        "risk_level": request.runtime_risk_level,
    }
    route_plan, trace = await TASK_PLANNER.plan(request.user_message, runtime_context=runtime_context)
    log_guhe_event("ROUTE_PLANNED", "TaskPlanner 已生成结构化 RoutePlan", observe=True, intent=route_plan.intent, source=route_plan.source, confidence=route_plan.confidence, required_tools=route_plan.required_tools, requires_workflow=route_plan.requires_workflow)
    clarification = build_clarification(request, route_plan)
    log_guhe_event("ROUTE_GUARDED", "路由计划已通过确定性澄清与风险边界", observe=True, needs_clarification=clarification is not None, risk_level=route_plan.risk_level)
    citations = [] if clarification else build_citations(route_plan)
    if citations:
        log_guhe_event("RAG_CITATIONS", "知识路径已生成引用", observe=True, citation_count=len(citations))
    tool_calls = [] if clarification else execute_allowed_light_path(request, route_plan)
    if tool_calls:
        log_guhe_event("TOOLS_EXECUTED", "允许的轻量工具路径执行完成", observe=True, tool_names=[call.action.tool_name for call in tool_calls], call_count=len(tool_calls))
    deterministic_answer, next_action = build_answer(route_plan, clarification, tool_calls, citations)
    model_result = compose_grounded_answer(
        user_message=request.user_message,
        deterministic_answer=deterministic_answer,
        facts={"route_plan": route_plan, "planner_trace": trace, "tool_calls": tool_calls},
        citations=citations,
        risk_level=route_plan.risk_level,
        next_action=next_action,
        skip_model=clarification is not None,
        skip_reason="clarification_required",
    )
    answer = model_result.answer

    summary = [] if request.reasoning_view == "off" else reasoning_summary(route_plan, trace)
    return ChatResponse(
        session_id=request.session_id,
        answer=answer,
        route_plan=route_plan,
        planner_trace=trace,
        citations=citations,
        tool_calls=tool_calls,
        clarification=clarification,
        next_action=next_action,
        risk_level=route_plan.risk_level,
        needs_human_approval=route_plan.risk_level == "high",
        reasoning_summary=summary,
        session_state={
            "agent_version": "step-25-task-planner-route-plan",
            "route_plan": route_plan.model_dump(),
            "planner_trace": trace.model_dump(),
            "model_answer": model_result.model_dump(),
            "runtime_context": {
                "runtime_user_id": request.runtime_user_id,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
        },
    )
