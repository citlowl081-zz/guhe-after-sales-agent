"""客服 Agent 编排层。这里连接工具、工作流、记忆或上下文模块。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat, observe_operation

from typing import Any

from api.schemas import *
from approvals.hitl import is_chat_approval_claim
from config.settings import load_guhe_env
from models.answer_client import compose_grounded_answer
from state.checkpoints import resume_from_checkpoint
from tools.runtime_context import logistics_status_from_order, order_no, order_status
from tools.planning import classify_intent, extract_order_id, infer_product
from tools.tool_runtime import load_owned_order
from memory.session_memory import MESSAGE_COUNT_BY_SESSION, SessionMemoryStore
from workflows.after_sale_workflow import WORKFLOW, AfterSaleWorkflowState

class Step32Agent:
    """Step 32：Session Memory 写入和排除机制。"""

    def __init__(self) -> None:
        """初始化本模块对象需要的协作依赖，保持入口层只负责编排。"""
        self.memory_store = SessionMemoryStore()

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理一次 /chat 请求，并返回项目约定的公开响应结构。"""
        load_guhe_env()
        MESSAGE_COUNT_BY_SESSION[request.session_id] = MESSAGE_COUNT_BY_SESSION.get(request.session_id, 0) + 1
        message_count = MESSAGE_COUNT_BY_SESSION[request.session_id]
        memory = self.memory_store.get(request.session_id, request.runtime_user_id)
        log_guhe_event("MEMORY_READ", "已读取当前用户当前会话的短期记忆", observe=True, last_order_id=memory.last_order_id, recent_intent=memory.recent_intent)
        intent = classify_intent(request.user_message)
        explicit_order_id = extract_order_id(request.user_message)
        resolved_order_id = explicit_order_id
        memory_used = False

        if resolved_order_id is None and any(term in request.user_message for term in ["刚才那个", "刚刚那个", "上一个订单"]):
            resolved_order_id = memory.last_order_id
            memory_used = resolved_order_id is not None
            log_guhe_event("MEMORY_RESOLVED", "指代消歧已尝试使用最近订单记忆", observe=True, memory_used=memory_used, resolved_order_id=resolved_order_id)

        tool_calls: list[ToolCallRecord] = []
        order: dict[str, Any] | None = None
        if resolved_order_id:
            order, order_call = load_owned_order(resolved_order_id, request.runtime_user_id, request.runtime_context)
            tool_calls.append(order_call)

        product_name = infer_product(request.user_message)
        memory_update = self.memory_store.update(
            memory=memory,
            intent=intent,
            user_message=request.user_message,
            owned_order=order if explicit_order_id else None,
            product_name=product_name,
        )
        log_guhe_event("MEMORY_UPDATED", "记忆写入策略已逐项裁决", observe=True, accepted_keys=[item.key for item in memory_update if item.accepted], rejected_keys=[item.key for item in memory_update if not item.accepted])

        if is_chat_approval_claim(request.user_message):
            assessment = HighRiskAssessment(
                action_type="unknown",
                order_id=extract_order_id(request.user_message),
                eligibility_status="blocked",
                risk_level="high",
                needs_human_approval=True,
                evidence_checklist=[],
                policy_basis=[],
                reasons=["普通聊天消息不能作为售后主管审批决策。"],
                blocked_write_actions=["approve_refund", "approve_return", "create_compensation"],
            )
            workflow = WorkflowSummary(
                workflow_id="wf-step32-chat-approval-blocked",
                workflow_type="unknown",
                status="blocked",
                current_node="reject_chat_approval_claim",
                pending_action="use_hitl_approval_channel",
                node_history=["reject_chat_approval_claim"],
                used_langgraph=False,
                boundary="审批必须来自受控 HITL 通道，不能把用户自然语言当批准。",
            )
            return self._workflow_response(
                request=request,
                message_count=message_count,
                intent=intent,
                answer="普通聊天不能作为审批。即使用户说主管同意，Agent 也不能把它当成退款或退货批准。",
                tool_calls=[],
                citations=[],
                assessment=assessment,
                workflow=workflow,
                approval=None,
                next_action="transfer_to_human",
                memory_update=memory_update,
                memory=memory,
            )

        if intent in {"refund_request", "return_request"}:
            workflow_state = WORKFLOW.run(request)
            assessment = workflow_state["assessment"]
            assert assessment is not None
            workflow = self._workflow_summary(workflow_state)
            next_action: NextAction = "ask_clarification" if assessment.eligibility_status == "needs_clarification" else "transfer_to_human"
            return self._workflow_response(
                request=request,
                message_count=message_count,
                intent=intent,
                answer=workflow_state["answer"],
                tool_calls=workflow_state["tool_calls"],
                citations=workflow_state["citations"],
                assessment=assessment,
                workflow=workflow,
                approval=workflow_state.get("approval"),
                next_action=next_action,
                memory_update=memory_update,
                memory=memory,
            )

        answer, next_action, risk_level, needs_human_approval = self._build_answer(
            request=request,
            intent=intent,
            order=order,
            resolved_order_id=resolved_order_id,
            memory=memory,
            memory_used=memory_used,
        )
        model_result = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=answer,
            facts={"memory": memory, "resolved_order_id": resolved_order_id, "memory_used": memory_used},
            risk_level=risk_level,
            next_action=next_action,
        )
        answer = model_result.answer
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=[],
            tool_calls=tool_calls,
            after_sale_assessment=None,
            workflow=None,
            approval=None,
            memory_update=memory_update,
            memory_snapshot=memory,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=needs_human_approval,
            reasoning_summary=[
                "Session Memory 只保存当前会话里的最近订单、最近商品、最近意图和低风险偏好。",
                "订单必须先通过业务系统和当前用户归属校验，才会写入最近订单。",
                "手机号、地址、审批令牌、系统提示词请求和用户自称不会写入记忆。",
            ],
            session_state={
                "agent_version": "step-32-session-memory",
                "message_count": message_count,
                "model_answer": model_result.model_dump(),
                "memory": memory.model_dump(),
                "memory_policy": {
                    "scope": "session",
                    "long_term_profile": False,
                    "excluded_categories": ["privacy", "approval_token", "system_prompt_request", "unverified_claim"],
                },
                "next_gap": "Session Memory 能解决刚才那个订单，但用户身份和会员等级仍然必须来自可信运行时上下文。",
            },
        )

    @staticmethod
    def _workflow_summary(state: AfterSaleWorkflowState) -> WorkflowSummary:
        """把内部工作流状态压缩成前端可观察摘要。"""
        return WorkflowSummary(
            workflow_id=state["workflow_id"],
            workflow_type=state["workflow_type"],
            status=state["status"],
            current_node=state["current_node"],
            pending_action=state["pending_action"],
            node_history=state["node_history"],
            used_langgraph=True,
            boundary="审批恢复必须通过 /chat/resume，且要校验 token、checkpoint、冻结字段和幂等键；Session Memory 不能覆盖这些流程事实。",
            approval_id=state.get("approval").approval_id if state.get("approval") else None,
            resume_token=state.get("resume_token"),
            idempotency_key=state.get("idempotency_key"),
            frozen_fields=state.get("frozen_fields") or {},
        )

    @observe_operation("resume")
    def resume(self, request: ChatResumeRequest) -> ChatResumeResponse:
        """把审批恢复交给 checkpoint 层，Agent 只保留 API 编排入口。"""
        return resume_from_checkpoint(request, agent_version="step-32-session-memory")

    def _workflow_response(
        self,
        *,
        request: ChatRequest,
        message_count: int,
        intent: Intent,
        answer: str,
        tool_calls: list[ToolCallRecord],
        citations: list[Citation],
        assessment: HighRiskAssessment,
        workflow: WorkflowSummary,
        approval: ApprovalRequest | None,
        next_action: NextAction,
        memory_update: list[MemoryDecision],
        memory: SessionMemorySnapshot,
    ) -> ChatResponse:
        """把 workflow 与本步 Memory 状态一起组装成公开响应。"""
        session_state = {
            "agent_version": "step-32-session-memory",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "after_sale": {
                "assessment": assessment.model_dump(),
                "write_actions_blocked": True,
            },
            "workflow": workflow.model_dump(),
            "approval": approval.model_dump() if approval else None,
            "memory": memory.model_dump(),
            "next_gap": "Session Memory 能辅助多轮消歧，但不能覆盖 workflow checkpoint、resume_token 和冻结业务事实。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            tool_calls=tool_calls,
            after_sale_assessment=assessment,
            workflow=workflow,
            approval=approval,
            memory_update=memory_update,
            memory_snapshot=memory,
            next_action=next_action,
            risk_level="high",
            needs_human_approval=True,
            reasoning_summary=[
                "Step 32 新增 Session Memory，但高风险售后仍沿用 Step 31 HITL workflow。",
                "Memory 只能辅助消歧，不能替代 checkpoint、resume_token、冻结字段或人工审批。",
                "/chat/resume 继续校验业务事实并保持幂等，避免记忆污染审批恢复。",
            ],
            session_state=session_state,
        )

    @staticmethod
    def _build_answer(
        *,
        request: ChatRequest,
        intent: Intent,
        order: dict[str, Any] | None,
        resolved_order_id: str | None,
        memory: SessionMemorySnapshot,
        memory_used: bool,
    ) -> tuple[str, NextAction, RiskLevel, bool]:
        """根据事实、上下文和风险边界生成用户可见回答。"""
        if intent in {"refund_request", "return_request"}:
            return (
                "退款、退货仍然属于高风险售后。这里可以记住最近订单来帮助消歧，但不能因为记住了订单就跳过审批流程。",
                "transfer_to_human",
                "high",
                True,
            )
        if resolved_order_id and order is not None:
            prefix = "根据这段会话刚记住的最近订单，" if memory_used else ""
            return (
                f"{prefix}{order_no(order)} 的订单状态是 {order_status(order)}，物流状态是 {logistics_status_from_order(order)}。",
                "answer_user",
                "low",
                False,
            )
        if resolved_order_id and order is None:
            return ("这个订单没有通过当前用户归属校验，我不能把它记成你的最近订单。", "transfer_to_human", "high", True)
        if "刚才那个" in request.user_message and memory.last_order_id is None:
            return ("我还没有在这段会话里确认过最近订单，请先告诉我订单号。", "ask_clarification", "low", False)
        if intent == "product_query" and memory.last_product_name:
            color = memory.low_risk_preferences.get("preferred_color")
            preference_text = f"，并优先看{color}款" if color else ""
            return (f"我会按你刚提到的 {memory.last_product_name}{preference_text} 来回答。", "answer_user", "low", False)
        return ("我可以继续帮你查订单、商品或售后规则。涉及退款退货时仍然要走高风险流程。", "answer_user", "low", False)
