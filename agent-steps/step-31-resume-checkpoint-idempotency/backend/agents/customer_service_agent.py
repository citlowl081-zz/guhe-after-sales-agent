"""客服 Agent 编排层。这里连接工具、工作流、记忆或上下文模块。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat, observe_operation

from typing import Any

from api.schemas import *
from approvals.hitl import is_chat_approval_claim
from config.settings import load_guhe_env
from models.answer_client import compose_grounded_answer
from state.checkpoints import resume_from_checkpoint
from tools.planning import classify_intent, extract_order_id
from tools.runtime_context import public_runtime_context
from workflows.after_sale_workflow import WORKFLOW, AfterSaleWorkflowState

class Step31Agent:
    """Step 31：用 resume、checkpoint 和幂等保护审批恢复。"""

    def __init__(self) -> None:
        """初始化本模块对象需要的协作依赖，保持入口层只负责编排。"""
        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理一次 /chat 请求，并返回项目约定的公开响应结构。"""
        load_guhe_env()
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别售后与审批相关意图", observe=True, intent=intent)
        if is_chat_approval_claim(request.user_message):
            log_guhe_event("CHAT_APPROVAL_BLOCKED", "普通聊天中的批准声明已被拒绝", observe=True)
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
                workflow_id="wf-step31-chat-approval-blocked",
                workflow_type="unknown",
                status="blocked",
                current_node="reject_chat_approval_claim",
                pending_action="use_hitl_approval_channel",
                node_history=["reject_chat_approval_claim"],
                used_langgraph=False,
                boundary="审批必须来自受控 HITL 通道，不能把用户自然语言当批准。",
            )
            return self._response(
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
            )
        workflow_state = WORKFLOW.run(request)
        log_guhe_event("CHECKPOINT_READY", "工作流已暂停并保存恢复凭据", observe=True, workflow_id=workflow_state["workflow_id"], has_resume_token=bool(workflow_state.get("resume_token")))
        assessment = workflow_state["assessment"]
        assert assessment is not None
        workflow = self._workflow_summary(workflow_state)
        next_action: NextAction = "ask_clarification" if assessment.eligibility_status == "needs_clarification" else "transfer_to_human"
        answer = workflow_state["answer"]
        return self._response(
            request=request,
            message_count=message_count,
            intent=intent,
            answer=answer,
            tool_calls=workflow_state["tool_calls"],
            citations=workflow_state["citations"],
            assessment=assessment,
            workflow=workflow,
            approval=workflow_state.get("approval"),
            next_action=next_action,
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
            boundary="审批恢复必须通过 /chat/resume，且要校验 token、checkpoint、冻结字段和幂等键。",
            approval_id=state.get("approval").approval_id if state.get("approval") else None,
            resume_token=state.get("resume_token"),
            idempotency_key=state.get("idempotency_key"),
            frozen_fields=state.get("frozen_fields") or {},
        )

    @observe_operation("resume")
    def resume(self, request: ChatResumeRequest) -> ChatResumeResponse:
        """把审批恢复交给 checkpoint 层，Agent 只保留 API 编排入口。"""
        result = resume_from_checkpoint(request, agent_version="step-31-resume-checkpoint-idempotency")
        log_guhe_event("WORKFLOW_RESUMED", "审批恢复校验与幂等处理完成", observe=True, accepted=result.resume_result.accepted, reason=result.resume_result.reason, idempotent_replay=result.resume_result.idempotent_replay)
        return result

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
        workflow: WorkflowSummary,
        approval: ApprovalRequest | None,
        next_action: NextAction,
    ) -> ChatResponse:
        """把内部判断结果组装成项目 API 响应。"""
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
            "agent_version": "step-31-resume-checkpoint-idempotency",
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
            "workflow": workflow.model_dump(),
            "approval": approval.model_dump() if approval else None,
            "next_gap": "高风险流程已经能恢复和防重复提交；下一幕要面对多轮上下文、用户身份和记忆边界带来的新污染。",
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
            next_action=next_action,
            risk_level="high" if intent in {"refund_request", "return_request"} else "low",
            needs_human_approval=True if intent in {"refund_request", "return_request"} else False,
            reasoning_summary=[
                "资格通过后保存 checkpoint，并把 workflow_id、resume_token 和冻结字段返回给审批恢复使用。",
                "/chat/resume 会二次校验业务事实，避免上下文漂移后继续沿用旧审批。",
                "幂等键保证重复恢复不会重复提交退款或退货申请。",
            ],
            session_state=session_state,
        )
