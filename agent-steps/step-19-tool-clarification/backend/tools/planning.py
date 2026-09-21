"""Step 19：工具规划与澄清判断。这里决定要不要调用工具、缺什么参数、是否需要先问用户。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from tools.contracts import *
from tools.runtime_context import *
from tools.runtime_context import _order_no, _runtime_context
from integrations.ecommerce_client import *


def normalize_intent(value: Any) -> Intent:
    """执行 normalize_intent 对应的项目逻辑。"""
    return value if value in {"general_chat", "product_consult", "order_query", "refund_status_query", "unknown"} else "unknown"

def normalize_tool_name(value: Any, tool_specs: dict[str, ToolSpec]) -> str | None:
    """执行 normalize_tool_name 对应的项目逻辑。"""
    if value in (None, "", "null", "none", "None"):
        return None
    text = str(value).strip()
    return text if text in tool_specs else None

def normalize_month(value: Any) -> int | None:
    """执行 normalize_month 对应的项目逻辑。"""
    if isinstance(value, int):
        month = value
    else:
        match = re.search(r"\d{1,2}", str(value))
        month = int(match.group(0)) if match else 0
    return month if 1 <= month <= 12 else None

def normalize_known_arguments(tool_name: str | None, raw_arguments: Any, tool_specs: dict[str, ToolSpec]) -> dict[str, Any]:
    """执行 normalize_known_arguments 对应的项目逻辑。"""
    if not tool_name or tool_name not in tool_specs or not isinstance(raw_arguments, dict):
        return {}

    spec = tool_specs[tool_name]
    allowed_fields = set(spec.parameters_schema)
    arguments: dict[str, Any] = {}

    if "order_id" in allowed_fields and raw_arguments.get("order_id"):
        order_id = str(raw_arguments["order_id"]).strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{5,}", order_id):
            arguments["order_id"] = order_id
    if "sku" in allowed_fields and raw_arguments.get("sku"):
        sku = str(raw_arguments["sku"]).strip().upper()
        if re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,}", sku):
            arguments["sku"] = sku
    if "month" in allowed_fields and raw_arguments.get("month") is not None:
        month = normalize_month(raw_arguments["month"])
        if month is not None:
            arguments["month"] = month

    return arguments

def normalize_missing_required(value: Any) -> list[str]:
    """执行 normalize_missing_required 对应的项目逻辑。"""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if item not in (None, "") and str(item).strip()]

def build_validated_clarification_plan(
    payload: dict[str, Any],
    tool_specs: dict[str, ToolSpec],
    model_name: str | None,
) -> ClarificationPlan:
    """执行 build_validated_clarification_plan 对应的项目逻辑。"""
    tool_name = normalize_tool_name(payload.get("tool_name"), tool_specs)
    arguments = normalize_known_arguments(tool_name, payload.get("known_arguments"), tool_specs)
    if tool_name:
        missing_required = find_missing_required_fields(tool_specs[tool_name], arguments)
    else:
        missing_required = normalize_missing_required(payload.get("missing_required"))

    return ClarificationPlan(
        intent=normalize_intent(payload.get("intent")),
        tool_name=tool_name,
        known_arguments=arguments,
        missing_required=missing_required,
        clarification_question=str(payload.get("clarification_question") or "").strip() or None,
        confidence=float(payload.get("confidence") or 0.7),
        reason=str(payload.get("reason") or "LLM 规划澄清和工具候选。"),
        model_name=model_name,
    )

def enrich_plan_with_runtime_context(request: ChatRequest, plan: ClarificationPlan) -> ClarificationPlan:
    """执行 enrich_plan_with_runtime_context 对应的项目逻辑。"""
    if plan.tool_name not in {"get_order_logistics", "get_refund_status"}:
        return plan
    if plan.known_arguments.get("order_id"):
        return plan

    context = _runtime_context(request)
    related_order_no = str(context.get("relatedOrderNo") or "").strip()
    orders = current_user_orders(request)
    contextual_order_no = related_order_no
    if not contextual_order_no and len(orders) == 1:
        contextual_order_no = _order_no(orders[0])
    if not contextual_order_no:
        return plan

    known_arguments = {**plan.known_arguments, "order_id": contextual_order_no}
    return plan.model_copy(
        update={
            "known_arguments": known_arguments,
            "missing_required": find_missing_required_fields(TOOL_SPECS[plan.tool_name], known_arguments),
            "reason": f"{plan.reason} 已使用电商后端校验过的当前页面/当前用户订单上下文补全 order_id。",
        }
    )

def find_missing_required_fields(spec: ToolSpec, arguments: dict[str, Any]) -> list[str]:
    """执行 find_missing_required_fields 对应的项目逻辑。"""
    return [field for field in spec.required if not arguments.get(field)]

def pre_tool_clarification(request: ChatRequest, plan: ClarificationPlan) -> ClarificationRequest | None:
    """执行 pre_tool_clarification 对应的项目逻辑。"""
    if not plan.tool_name or plan.tool_name not in TOOL_SPECS:
        return None

    spec = TOOL_SPECS[plan.tool_name]
    # Step 19 的关键：即使 LLM 已经给出 missing_required，后端仍按 ToolSpec 重新计算。
    missing = find_missing_required_fields(spec, plan.known_arguments)
    if not missing:
        return None

    clarification_field = missing[0]
    if clarification_field == "sku":
        return ClarificationRequest(
            clarification_field="sku",
            message=plan.clarification_question or "你想查哪款商品？请补充商品名或 SKU，我才能查询实时价格和库存。",
            candidates=[],
        )
    if clarification_field == "month":
        return ClarificationRequest(
            clarification_field="month",
            message=plan.clarification_question or "你想查哪个月份的订单？请补充月份，我才能先查询候选订单。",
            candidates=[],
        )
    candidates = user_order_candidates(request)
    return ClarificationRequest(
        clarification_field=clarification_field,
        message=plan.clarification_question or "你要查哪一个订单？请选择订单号，或直接补充订单号。",
        candidates=candidates,
    )

def plan_tool_action(plan: ClarificationPlan) -> ToolAction | None:
    """把意图和已知参数转换成结构化工具 Action。"""
    if not plan.tool_name or plan.tool_name not in TOOL_SPECS:
        return None
    spec = TOOL_SPECS[plan.tool_name]
    if find_missing_required_fields(spec, plan.known_arguments):
        return None
    return ToolAction(tool_name=plan.tool_name, arguments=plan.known_arguments, reason=plan.reason)

def post_tool_clarification(action: ToolAction | None, observation: ToolObservation | None) -> ClarificationRequest | None:
    """执行 post_tool_clarification 对应的项目逻辑。"""
    if not action or not observation or action.tool_name != "search_current_user_orders":
        return None
    candidates = [
        {
            "order_id": order["order_id"],
            "status": order["status"],
            "items": order["items"],
        }
        for order in observation.data.get("candidate_orders", [])
    ]
    if len(candidates) <= 1:
        return None
    return ClarificationRequest(
        clarification_field="order_id",
        message="我按你给的条件查到了多笔候选订单，请确认你要查哪一个订单号。",
        candidates=order_candidates_from_orders(candidates),
    )
