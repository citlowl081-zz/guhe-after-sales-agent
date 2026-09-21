"""Step 26：Agent 编排层。每步只在这里串联已经长出的模块。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from typing import Any, Literal

from api.schemas import *
from config.settings import *
from tools.runtime_context import *
from integrations.ecommerce_client import *
from tools.contracts import *
from tools.planning import *
from tools.tool_runtime import *
from models.answer_client import compose_grounded_answer
from policies.after_sale_policy import *


class Step26Agent:
    """Step 26：高风险动作只能做资格判断，不能被自然语言直接执行。"""

    def __init__(self) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给 Agent 编排。"""
        load_guhe_env()
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别售后动作意图", observe=True, intent=intent)
        order_id = extract_order_id(request.user_message)
        action_type: Literal["refund", "return", "unknown"] = "return" if intent == "return_request" else "refund" if intent == "refund_request" else "unknown"
        log_guhe_event("ACTION_BOUNDARY", "已区分退款、退货与未知高风险动作", observe=True, action_type=action_type, has_order_id=bool(order_id))

        if not order_id:
            assessment = HighRiskAssessment(
                action_type=action_type,
                order_id=None,
                eligibility_status="needs_clarification",
                risk_level="high",
                needs_human_approval=True,
                evidence_checklist=[],
                policy_basis=[],
                reasons=["高风险售后必须先提供订单号，不能只凭一句自然语言执行。"],
                blocked_write_actions=["create_refund", "approve_refund", "cancel_order", "create_compensation"],
            )
            return self._response(
                request=request,
                message_count=message_count,
                intent=intent,
                answer="请先提供要处理的订单号。我可以帮你判断退款或退货资格，但不会直接退款、取消或补偿。",
                tool_calls=[],
                citations=[],
                assessment=assessment,
                next_action="ask_clarification",
            )

        order, order_call = load_order(order_id, request)
        log_guhe_event("ORDER_FACT_LOOKUP", "当前用户订单事实查询完成", observe=True, found=order is not None)
        tool_calls = [order_call]
        logistics_call: ToolCallRecord | None = None
        if order is not None:
            logistics_call = load_logistics(order, request.runtime_user_id)
            tool_calls.append(logistics_call)
        citations, policy_call = retrieve_policy(action_type)
        log_guhe_event("POLICY_RETRIEVED", "高风险动作规则检索完成", observe=True, citation_count=len(citations))
        tool_calls.append(policy_call)
        assessment = assess_refund_boundary(order, citations, action_type)
        log_guhe_event("RISK_ASSESSED", "确定性代码已完成高风险资格判断", observe=True, eligibility_status=assessment.eligibility_status, needs_human_approval=assessment.needs_human_approval)
        tool_calls.append(
            make_tool_call(
                "check_after_sale_boundary",
                {"order_id": order_id, "action_type": action_type},
                "最终只输出资格判断和边界，不执行写动作。",
                "已完成高风险动作边界判断。",
                {
                    "eligibility_status": assessment.eligibility_status,
                    "needs_human_approval": assessment.needs_human_approval,
                    "blocked_write_actions": assessment.blocked_write_actions,
                },
            )
        )

        if assessment.eligibility_status == "eligible_for_application":
            answer = (
                f"订单 {order_id} 目前只判断为可以发起售后申请。"
                "我不会因为你一句“直接给我退”就执行退款、取消订单或补偿；后续必须进入受控售后流程。"
            )
        else:
            answer = (
                f"订单 {order_id} 当前不能直接进入退款执行。"
                f"{assessment.reasons[0]}我只能解释依据并建议转人工复核。"
            )
        return self._response(
            request=request,
            message_count=message_count,
            intent=intent,
            answer=answer,
            tool_calls=tool_calls,
            citations=citations,
            assessment=assessment,
            next_action="transfer_to_human",
        )

    def _response(
        self,
        *,
        request: ChatRequest,
        message_count: int,
        intent: Intent,
        answer: str,
        tool_calls: list[ToolCallRecord],
        citations: list[Citation],
        assessment: HighRiskAssessment,
        next_action: NextAction,
    ) -> ChatResponse:
        """把内部评估或工作流状态组装成项目 API 的公开响应。"""
        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=answer,
            facts={"assessment": assessment, "tool_calls": tool_calls},
            citations=citations,
            workflow=workflow if "workflow" in locals() else None,
            risk_level="high" if intent in {"refund_request", "return_request"} else "low",
            next_action=next_action,
        )
        answer = model_result.answer

        session_state = {
            "agent_version": "step-26-high-risk-action-boundary",
            "message_count": message_count,
            "model_answer": model_result.model_dump(),
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "after_sale": {
                "assessment": assessment.model_dump(),
                "write_actions_blocked": True,
            },
            "next_gap": "现在只能做高风险边界判断；复杂售后步骤还没有被固定成 LangGraph 工作流。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            tool_calls=tool_calls,
            after_sale_assessment=assessment,
            next_action=next_action,
            risk_level="high" if intent in {"refund_request", "return_request"} else "low",
            needs_human_approval=True if intent in {"refund_request", "return_request"} else False,
            reasoning_summary=[
                "高风险售后不能由自然语言直接触发写动作。",
                "资格判断必须同时查看订单状态、物流状态和售后政策依据。",
                "本步还没有售后工作流、HITL、/chat/resume、Checkpoint 或幂等提交。",
            ],
            session_state=session_state,
        )

def health() -> dict[str, str]:
    """返回当前步程健康状态。"""
    return {"status": "ok", "step": "25"}

def capabilities() -> dict[str, Any]:
    """返回当前步程能力开关。"""
    return load_agent_capabilities()

def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给 Agent 编排。"""
    return agent.chat(request)
