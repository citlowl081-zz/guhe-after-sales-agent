"""Step 27：Agent 编排层。每步只在这里串联已经长出的模块。"""

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
from workflows.after_sale_workflow import WORKFLOW, AfterSaleWorkflowState


class Step27Agent:
    """Step 27：用 LangGraph 把售后流程节点画死。"""

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
        log_guhe_event("INTENT_CLASSIFIED", "已识别是否进入售后工作流", observe=True, intent=intent)
        workflow_state = WORKFLOW.run(request)
        log_guhe_event("WORKFLOW_COMPLETED", "LangGraph 售后流程已到达本步停止边界", observe=True, current_node=workflow_state["current_node"], node_count=len(workflow_state["node_history"]))
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
            next_action=next_action,
        )

    @staticmethod
    def _workflow_summary(state: AfterSaleWorkflowState) -> WorkflowSummary:
        """把 LangGraph 状态压缩成前端可观察的 workflow 摘要。"""
        return WorkflowSummary(
            workflow_id=state["workflow_id"],
            workflow_type=state["workflow_type"],
            status=state["status"],
            current_node=state["current_node"],
            pending_action=state["pending_action"],
            node_history=state["node_history"],
            used_langgraph=True,
            boundary="StateGraph 固定售后节点顺序；本步不提交申请、不审批、不返回 resume_token。",
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
        workflow: WorkflowSummary,
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
            "agent_version": "step-27-langgraph-workflow",
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
            "next_gap": "售后流程已经被 StateGraph 固定，但未发货退款和签收后退货还没有拆成各自更细的业务流程。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            tool_calls=tool_calls,
            after_sale_assessment=assessment,
            workflow=workflow,
            next_action=next_action,
            risk_level="high" if intent in {"refund_request", "return_request"} else "low",
            needs_human_approval=True if intent in {"refund_request", "return_request"} else False,
            reasoning_summary=[
                "高风险售后进入 LangGraph StateGraph，而不是交给自由 Agent 决定节点顺序。",
                "固定节点依次完成售后类型识别、订单校验、物流读取、政策检索和资格判断。",
                "本步还没有 HITL、/chat/resume、Checkpoint 或幂等提交。",
            ],
            session_state=session_state,
        )

def health() -> dict[str, str]:
    """返回当前步程健康状态。"""
    return {"status": "ok", "step": "26"}

def capabilities() -> dict[str, Any]:
    """返回当前步程能力开关。"""
    return load_agent_capabilities()

def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给 Agent 编排。"""
    return agent.chat(request)
