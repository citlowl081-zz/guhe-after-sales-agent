"""HITL 恢复层。Context Builder 只组织上下文，不替代 checkpoint 恢复。"""

from __future__ import annotations

from typing import Any, Literal

from api.schemas import *
from integrations.ecommerce_client import order_fact_from_ecommerce
from memory.session_memory import SUBMITTED_ACTIONS, WORKFLOW_CHECKPOINTS
from tools.runtime_context import logistics_status_from_order, order_no, order_status


def build_resume_token(session_id: str, workflow_id: str, order_id: str | None) -> str:
    return f"resume-{session_id}-{workflow_id}-{order_id or 'missing'}"


def freeze_workflow_fields(request: ChatRequest, order: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_user_id": request.runtime_user_id,
        "order_id": order_no(order),
        "order_status": order_status(order),
        "logistics_status": logistics_status_from_order(order),
    }


def create_workflow_checkpoint(request: ChatRequest, workflow: WorkflowState, order: dict[str, Any]) -> WorkflowState:
    workflow.approval_id = f"approval-{workflow.workflow_id}"
    workflow.resume_token = build_resume_token(request.session_id, workflow.workflow_id, order_no(order))
    workflow.idempotency_key = f"idemp-{workflow.workflow_id}"
    workflow.frozen_fields.update(freeze_workflow_fields(request, order))
    WORKFLOW_CHECKPOINTS[(request.session_id, workflow.workflow_id)] = {
        "workflow": workflow.model_dump(),
        "resume_token": workflow.resume_token,
        "idempotency_key": workflow.idempotency_key,
        "frozen_fields": workflow.frozen_fields,
        "order_snapshot": order,
    }
    return workflow


def business_recheck(checkpoint: dict[str, Any]) -> dict[str, Any]:
    frozen = checkpoint["frozen_fields"]
    order_id = frozen.get("order_id")
    current_order = order_fact_from_ecommerce(order_id, str(frozen["runtime_user_id"])) if order_id else None
    if current_order is None:
        current_order = checkpoint.get("order_snapshot")
    if current_order is None:
        return {"passed": False, "reason": "order_not_found", "mismatches": {"order_id": {"frozen": order_id, "current": None}}}
    current_values = {
        "order_status": order_status(current_order),
        "logistics_status": logistics_status_from_order(current_order),
    }
    mismatches = {
        field: {"frozen": frozen.get(field), "current": current_values.get(field)}
        for field in ("order_status", "logistics_status")
        if frozen.get(field) != current_values.get(field)
    }
    return {"passed": not mismatches, "reason": None if not mismatches else "business_fact_drift", "mismatches": mismatches}


def handle_resume_request(request: ChatResumeRequest, *, agent_version: str) -> ChatResumeResponse:
    checkpoint = WORKFLOW_CHECKPOINTS.get((request.session_id, request.workflow_id))
    if checkpoint is None:
        return _blocked(request, "没有找到匹配的 workflow checkpoint。", {"passed": False, "reason": "checkpoint_not_found"}, agent_version=agent_version)
    workflow = dict(checkpoint["workflow"])
    if request.resume_token != checkpoint["resume_token"]:
        return _blocked(request, "resume_token 不匹配，不能恢复这个审批流程。", {"passed": False, "reason": "invalid_resume_token"}, workflow, agent_version)
    if request.reviewer_role != "after_sale_manager":
        return _blocked(request, "只有售后主管角色可以恢复高风险审批。", {"passed": False, "reason": "invalid_reviewer_role"}, workflow, agent_version)
    if request.decision == "rejected":
        workflow.update({"status": "rejected", "pending_action": "notify_user"})
        return _accepted(request, "rejected", "售后主管已拒绝该申请，系统不会提交退款动作。", workflow, {"decision": "rejected", "accepted": True}, {"passed": True, "reason": "rejected_without_submission"}, agent_version)
    if request.decision == "needs_more_info":
        workflow.update({"status": "paused", "pending_action": "ask_user"})
        return _accepted(request, "paused", "售后主管要求补充信息，流程继续暂停。", workflow, {"decision": "needs_more_info", "accepted": True}, {"passed": True, "reason": "needs_more_info_without_submission"}, agent_version)
    recheck = business_recheck(checkpoint)
    if not recheck["passed"]:
        workflow.update({"status": "blocked", "pending_action": "transfer_to_human"})
        return _blocked(request, "订单事实已变化，不能继续提交退款动作。", recheck, workflow, agent_version)
    idempotency_key = checkpoint["idempotency_key"]
    result = SUBMITTED_ACTIONS.setdefault(idempotency_key, {"decision": "approved", "accepted": True, "submitted_action": "refund_application_created", "idempotency_key": idempotency_key})
    workflow.update({"status": "submitted", "pending_action": "notify_user"})
    return _accepted(request, "submitted", "售后主管已批准，系统已用幂等键提交退款申请。", workflow, result, recheck, agent_version)


def _blocked(request: ChatResumeRequest, answer: str, business_recheck: dict[str, Any], workflow: dict[str, Any] | None = None, agent_version: str = "step-34-context-builder") -> ChatResumeResponse:
    return ChatResumeResponse(session_id=request.session_id, workflow_id=request.workflow_id, status="blocked", answer=answer, workflow=workflow, result={"decision": request.decision, "accepted": False}, business_recheck=business_recheck, session_state={"agent_version": agent_version, "resume_boundary": "Context Builder 不能把聊天内容当审批结果。"})


def _accepted(request: ChatResumeRequest, status: Literal["submitted", "rejected", "paused"], answer: str, workflow: dict[str, Any], result: dict[str, Any], business_recheck: dict[str, Any], agent_version: str) -> ChatResumeResponse:
    WORKFLOW_CHECKPOINTS[(request.session_id, request.workflow_id)]["workflow"] = workflow
    return ChatResumeResponse(session_id=request.session_id, workflow_id=request.workflow_id, status=status, answer=answer, workflow=workflow, result=result, business_recheck=business_recheck, session_state={"agent_version": agent_version, "resume_boundary": "恢复前校验 checkpoint、token、角色、冻结事实和幂等键。"})
