"""Resume、checkpoint 和幂等状态。审批恢复前必须复核业务事实。"""

from __future__ import annotations

from typing import Any, Literal

from api.schemas import *
from integrations.ecommerce_client import business_order_snapshot
from tools.runtime_context import fulfillment_status, logistics_status_from_order, order_amount, order_user_id, payment_status

CHECKPOINTS: dict[tuple[str, str], dict[str, Any]] = {}

SUBMITTED_ACTIONS: dict[str, dict[str, Any]] = {}

def build_resume_token(workflow_id: str, assessment: HighRiskAssessment) -> str:
    """为暂停的审批流程生成恢复令牌。"""
    return "resume-{0}-{1}".format(workflow_id, assessment.order_id or "missing")

def build_idempotency_key(workflow_id: str, assessment: HighRiskAssessment) -> str:
    """为审批恢复生成幂等键，防止重复提交业务动作。"""
    return "hitl:{0}:{1}:{2}".format(workflow_id, assessment.action_type, assessment.order_id or "missing")

def freeze_workflow_fields(state: AfterSaleWorkflowState, assessment: HighRiskAssessment) -> dict[str, Any]:
    """冻结审批恢复前必须复核的关键业务字段。"""
    order = state.get("order") or {}
    return {
        "workflow_id": state["workflow_id"],
        "workflow_type": state["workflow_type"],
        "order_id": assessment.order_id,
        "runtime_user_id": state["runtime_user_id"],
        "order_status": fulfillment_status(order),
        "payment_status": payment_status(order),
        "logistics_status": logistics_status_from_order(order),
        "amount": str(order_amount(order) or ""),
        "policy_ids": [citation.policy_id for citation in assessment.policy_basis],
        "eligibility_status": assessment.eligibility_status,
    }

def save_checkpoint(
    *,
    session_id: str,
    workflow_id: str,
    workflow_type: str,
    approval: ApprovalRequest,
    assessment: HighRiskAssessment,
    resume_token: str,
    idempotency_key: str,
    frozen_fields: dict[str, Any],
    node_history: list[str],
) -> None:
    """保存暂停审批的公开 checkpoint，供 /chat/resume 恢复。"""
    CHECKPOINTS[(session_id, workflow_id)] = {
        "session_id": session_id,
        "workflow_id": workflow_id,
        "workflow_type": workflow_type,
        "approval": approval.model_dump(),
        "assessment": assessment.model_dump(),
        "resume_token": resume_token,
        "idempotency_key": idempotency_key,
        "frozen_fields": frozen_fields,
        "node_history": node_history,
        "status": "paused",
        "pending_action": "require_human_approval",
    }

def recheck_business_facts(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """恢复前重新校验业务事实，发现漂移就阻断提交。"""
    frozen = checkpoint["frozen_fields"]
    order_id = str(frozen["order_id"])
    current = business_order_snapshot(order_id, str(frozen["runtime_user_id"]))
    if current is None:
        return {"passed": False, "reason": "订单在恢复时不存在。", "current": None, "frozen": frozen}
    comparable = {
        "runtime_user_id": order_user_id(current),
        "order_status": fulfillment_status(current),
        "payment_status": payment_status(current),
        "logistics_status": logistics_status_from_order(current, current.get("_logistics") if isinstance(current.get("_logistics"), dict) else None),
        "amount": str(order_amount(current) or ""),
    }
    expected = {
        "runtime_user_id": frozen.get("runtime_user_id"),
        "order_status": frozen.get("order_status"),
        "payment_status": frozen.get("payment_status"),
        "logistics_status": frozen.get("logistics_status"),
        "amount": frozen.get("amount"),
    }
    mismatches = {key: {"frozen": expected[key], "current": comparable[key]} for key in expected if expected[key] != comparable[key]}
    return {
        "passed": not mismatches,
        "reason": "业务事实二次校验通过。" if not mismatches else "业务事实已变化，不能沿用旧审批结果。",
        "mismatches": mismatches,
        "current": comparable,
        "frozen": frozen,
    }

def submit_after_sale_action(checkpoint: dict[str, Any], reviewer_note: str | None) -> tuple[str, bool]:
    """模拟提交售后动作，并用幂等键识别重复恢复。"""
    idempotency_key = checkpoint["idempotency_key"]
    if idempotency_key in SUBMITTED_ACTIONS:
        return str(SUBMITTED_ACTIONS[idempotency_key]["request_id"]), True
    request_id = "asr-{0}".format(abs(hash(idempotency_key)) % 1_000_000)
    SUBMITTED_ACTIONS[idempotency_key] = {
        "request_id": request_id,
        "workflow_id": checkpoint["workflow_id"],
        "workflow_type": checkpoint["workflow_type"],
        "order_id": checkpoint["frozen_fields"]["order_id"],
        "reviewer_note": reviewer_note,
    }
    return request_id, False


def workflow_from_checkpoint(checkpoint: dict[str, Any]) -> WorkflowSummary:
    """从 checkpoint 还原公开 workflow 摘要。"""
    return WorkflowSummary(
        workflow_id=checkpoint["workflow_id"],
        workflow_type=checkpoint["workflow_type"],
        status=checkpoint["status"],
        current_node="stop_before_submission",
        pending_action=checkpoint["pending_action"],
        node_history=checkpoint["node_history"],
        used_langgraph=True,
        boundary="恢复必须校验 checkpoint、resume_token、冻结字段和幂等键。",
        approval_id=checkpoint["approval"]["approval_id"],
        resume_token=checkpoint["resume_token"],
        idempotency_key=checkpoint["idempotency_key"],
        frozen_fields=checkpoint["frozen_fields"],
    )


def resume_response(
    *,
    agent_version: str,
    request: ChatResumeRequest,
    status: Literal["completed", "paused", "rejected", "blocked"],
    answer: str,
    workflow: WorkflowSummary | None,
    approval: ApprovalRequest | None,
    result: ResumeResult,
    business_recheck: dict[str, Any],
) -> ChatResumeResponse:
    """构造恢复成功、阻断或幂等重放响应。"""
    return ChatResumeResponse(
        session_id=request.session_id,
        workflow_id=request.workflow_id,
        status=status,
        answer=answer,
        workflow=workflow,
        approval=approval,
        resume_result=result,
        business_recheck=business_recheck,
        session_state={
            "agent_version": agent_version,
            "workflow": workflow.model_dump() if workflow else None,
            "approval": approval.model_dump() if approval else None,
            "resume_result": result.model_dump(),
            "business_recheck": business_recheck,
        },
    )


def resume_blocked_response(
    request: ChatResumeRequest,
    *,
    agent_version: str,
    reason: str,
    business_recheck: dict[str, Any],
    workflow: WorkflowSummary | None = None,
    approval: ApprovalRequest | None = None,
) -> ChatResumeResponse:
    """构造恢复失败响应，确保不会提交业务动作。"""
    result = ResumeResult(decision=request.decision, accepted=False, reason=reason)
    return resume_response(
        agent_version=agent_version,
        request=request,
        status="blocked",
        answer=reason,
        workflow=workflow,
        approval=approval,
        result=result,
        business_recheck=business_recheck,
    )


def resume_from_checkpoint(request: ChatResumeRequest, *, agent_version: str) -> ChatResumeResponse:
    """恢复暂停审批，集中校验令牌、角色、业务事实和幂等提交。"""
    checkpoint = CHECKPOINTS.get((request.session_id, request.workflow_id))
    if checkpoint is None:
        return resume_blocked_response(
            request,
            agent_version=agent_version,
            reason="没有找到匹配 session_id 和 workflow_id 的 checkpoint。",
            business_recheck={"passed": False, "reason": "checkpoint_not_found"},
        )
    workflow = workflow_from_checkpoint(checkpoint)
    approval = ApprovalRequest.model_validate(checkpoint["approval"])
    if request.resume_token != checkpoint["resume_token"]:
        return resume_blocked_response(
            request,
            agent_version=agent_version,
            reason="resume_token 不匹配，不能恢复这个审批流程。",
            workflow=workflow,
            approval=approval,
            business_recheck={"passed": False, "reason": "invalid_resume_token"},
        )
    if request.reviewer_role != "after_sale_manager":
        return resume_blocked_response(
            request,
            agent_version=agent_version,
            reason="只有售后主管角色可以恢复高风险审批。",
            workflow=workflow,
            approval=approval,
            business_recheck={"passed": False, "reason": "invalid_reviewer_role"},
        )
    if request.decision == "rejected":
        workflow.status = "rejected"
        workflow.pending_action = "notify_user"
        result = ResumeResult(decision="rejected", accepted=True, reason="人工审批拒绝，未提交业务申请。")
        return resume_response(
            agent_version=agent_version,
            request=request,
            status="rejected",
            answer="售后主管已拒绝该申请，系统不会提交退款或退货动作。",
            workflow=workflow,
            approval=approval,
            result=result,
            business_recheck={"passed": True, "reason": "rejected_without_submission"},
        )
    if request.decision == "needs_more_info":
        workflow.status = "paused"
        workflow.pending_action = "ask_user"
        result = ResumeResult(decision="needs_more_info", accepted=True, reason="人工审批要求补充信息，流程继续暂停。")
        return resume_response(
            agent_version=agent_version,
            request=request,
            status="paused",
            answer="售后主管要求补充信息，当前流程不会提交业务申请。",
            workflow=workflow,
            approval=approval,
            result=result,
            business_recheck={"passed": True, "reason": "needs_more_info_without_submission"},
        )

    business_recheck = recheck_business_facts(checkpoint)
    if not business_recheck["passed"]:
        workflow.status = "blocked"
        workflow.pending_action = "transfer_to_human"
        result = ResumeResult(decision="approved", accepted=False, reason=business_recheck["reason"])
        return resume_response(
            agent_version=agent_version,
            request=request,
            status="blocked",
            answer="恢复审批时业务事实已经变化，我不会沿用旧审批结果提交申请，请人工重新核验。",
            workflow=workflow,
            approval=approval,
            result=result,
            business_recheck=business_recheck,
        )

    request_id, replay = submit_after_sale_action(checkpoint, request.reviewer_note)
    workflow.status = "completed"
    workflow.pending_action = "notify_user"
    result = ResumeResult(
        decision="approved",
        accepted=True,
        idempotent_replay=replay,
        request_id=request_id,
        reason="人工审批通过，已按幂等键提交售后申请。" if not replay else "重复恢复命中同一幂等键，未重复提交。",
    )
    action_label = "退款" if workflow.workflow_type == "unshipped_refund" else "退货"
    answer = "售后主管已批准，系统已提交{0}申请 {1}。".format(action_label, request_id)
    if replay:
        answer = "这次恢复命中了已提交记录，系统没有重复提交；沿用申请 {0}。".format(request_id)
    return resume_response(
        agent_version=agent_version,
        request=request,
        status="completed",
        answer=answer,
        workflow=workflow,
        approval=approval,
        result=result,
        business_recheck=business_recheck,
    )
