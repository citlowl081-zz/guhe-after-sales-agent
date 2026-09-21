"""Step 27：售后工作流层。LangGraph 风格节点顺序在这里固定。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from guhe_runtime.guhe_logging import log_guhe_event

from api.schemas import *
from tools.runtime_context import runtime_context
from tools.planning import classify_intent, extract_order_id
from tools.tool_runtime import load_order, load_logistics, make_tool_call
from policies.after_sale_policy import retrieve_policy, assess_refund_boundary


class AfterSaleWorkflowState(TypedDict, total=False):
    """AfterSaleWorkflowState 承载本步逐步长出的 Agent 模块能力。"""
    session_id: str
    runtime_user_id: str
    runtime_context: dict[str, Any]
    user_message: str
    workflow_id: str
    workflow_type: Literal["unshipped_refund", "received_return", "unknown"]
    action_type: Literal["refund", "return", "unknown"]
    order_id: str | None
    order: dict[str, Any] | None
    citations: list[Citation]
    tool_calls: list[ToolCallRecord]
    assessment: HighRiskAssessment | None
    status: Literal["running", "completed", "blocked"]
    current_node: str
    pending_action: str
    node_history: list[str]
    answer: str

class AfterSaleWorkflow:
    """Step 27 的显式售后 StateGraph。

    项目重点：这一版先把复杂售后关进固定节点顺序。它只产出流程状态和边界说明，
    不提交申请、不暂停审批，也不返回恢复令牌。
    """

    def __init__(self) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        self.graph = self._build_graph()

    def run(self, request: ChatRequest) -> AfterSaleWorkflowState:
        """为本轮请求初始化工作流状态，并交给 LangGraph 固定节点执行。"""
        order_id = extract_order_id(request.user_message)
        intent = classify_intent(request.user_message)
        action_type: Literal["refund", "return", "unknown"] = "return" if intent == "return_request" else "refund" if intent == "refund_request" else "unknown"
        workflow_id = "wf-step26-{0}-{1}".format(request.session_id, order_id or "missing")
        initial_state: AfterSaleWorkflowState = {
            "session_id": request.session_id,
            "runtime_user_id": request.runtime_user_id,
            "runtime_context": runtime_context(request),
            "user_message": request.user_message,
            "workflow_id": workflow_id,
            "workflow_type": "unknown",
            "action_type": action_type,
            "order_id": order_id,
            "order": None,
            "citations": [],
            "tool_calls": [],
            "assessment": None,
            "status": "running",
            "current_node": "classify_after_sale_intent",
            "pending_action": "run_workflow",
            "node_history": [],
            "answer": "",
        }
        return self.graph.invoke(initial_state)

    def _build_graph(self):
        """注册售后 StateGraph 节点和条件边，让流程顺序由代码固定。"""
        graph = StateGraph(AfterSaleWorkflowState)
        graph.add_node("classify_after_sale_intent", self._classify_after_sale_intent)
        graph.add_node("load_order", self._load_order)
        graph.add_node("load_logistics", self._load_logistics)
        graph.add_node("retrieve_policy", self._retrieve_policy)
        graph.add_node("check_eligibility", self._check_eligibility)
        graph.add_node("stop_before_submission", self._stop_before_submission)
        graph.set_entry_point("classify_after_sale_intent")
        graph.add_conditional_edges(
            "classify_after_sale_intent",
            self._route_after_classify,
            {"load_order": "load_order", "stop_before_submission": "stop_before_submission"},
        )
        graph.add_conditional_edges(
            "load_order",
            self._route_after_order,
            {"load_logistics": "load_logistics", "stop_before_submission": "stop_before_submission"},
        )
        graph.add_edge("load_logistics", "retrieve_policy")
        graph.add_edge("retrieve_policy", "check_eligibility")
        graph.add_edge("check_eligibility", "stop_before_submission")
        graph.add_edge("stop_before_submission", END)
        return graph.compile()

    def _classify_after_sale_intent(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """把用户售后诉求映射成工作流类型，决定是否继续进入节点流。"""
        workflow_type: Literal["unshipped_refund", "received_return", "unknown"]
        if state["action_type"] == "refund":
            workflow_type = "unshipped_refund"
        elif state["action_type"] == "return":
            workflow_type = "received_return"
        else:
            workflow_type = "unknown"
        return self._complete(state, "classify_after_sale_intent", {"workflow_type": workflow_type})

    def _load_order(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """读取并校验订单事实；归属或事实失败时立刻停在人工边界。"""
        order, order_call = load_order(str(state["order_id"]), state["runtime_user_id"], state.get("runtime_context") or {})
        updates: dict[str, Any] = {"order": order, "tool_calls": [*state["tool_calls"], order_call]}
        if order is None:
            updates.update(
                {
                    "status": "blocked",
                    "pending_action": "transfer_to_human",
                    "answer": "订单事实或归属没有通过校验，售后流程不能继续。",
                }
            )
        return self._complete(state, "load_order", updates)

    def _load_logistics(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """读取物流状态，给后续资格判断提供发货和签收证据。"""
        order = state["order"]
        if order is None:
            return self._complete(state, "load_logistics")
        return self._complete(state, "load_logistics", {"tool_calls": [*state["tool_calls"], load_logistics(order, state["runtime_user_id"])]})

    def _retrieve_policy(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """检索售后政策依据，让资格判断有可引用的规则来源。"""
        citations, policy_call = retrieve_policy(state["action_type"])
        return self._complete(
            state,
            "retrieve_policy",
            {"citations": citations, "tool_calls": [*state["tool_calls"], policy_call]},
        )

    def _check_eligibility(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """把订单、物流和政策证据收口为高风险资格判断。"""
        assessment = assess_refund_boundary(state.get("order"), state["citations"], state["action_type"])
        tool_call = make_tool_call(
            "check_after_sale_boundary",
            {"order_id": state.get("order_id"), "action_type": state["action_type"]},
            "StateGraph 固定节点流后，在资格节点统一收口。",
            "已完成售后资格判断，但还没有进入申请提交或人工审批。",
            {
                "eligibility_status": assessment.eligibility_status,
                "needs_human_approval": assessment.needs_human_approval,
                "blocked_write_actions": assessment.blocked_write_actions,
            },
        )
        return self._complete(state, "check_eligibility", {"assessment": assessment, "tool_calls": [*state["tool_calls"], tool_call]})

    def _stop_before_submission(self, state: AfterSaleWorkflowState) -> dict[str, Any]:
        """在提交申请或审批前停止流程，公开当前边界和下一步建议。"""
        assessment = state.get("assessment")
        if assessment is None:
            assessment = HighRiskAssessment(
                action_type=state["action_type"],
                order_id=state.get("order_id"),
                eligibility_status="needs_clarification",
                risk_level="high",
                needs_human_approval=True,
                evidence_checklist=[],
                policy_basis=[],
                reasons=["缺少订单号或售后类型，流程停在澄清边界。"],
                blocked_write_actions=["create_refund", "approve_refund", "cancel_order", "create_compensation"],
            )
        status: Literal["completed", "blocked"] = "blocked" if assessment.eligibility_status in {"blocked", "needs_clarification"} else "completed"
        answer = (
            "售后流程已经按固定节点完成资格判断。"
            "这一步只证明高风险动作必须进入显式工作流，不提交申请、不审批，也不恢复。"
        )
        return self._complete(
            state,
            "stop_before_submission",
            {
                "assessment": assessment,
                "status": status,
                "pending_action": "explain_boundary",
                "answer": state.get("answer") or answer,
            },
        )

    @staticmethod
    def _route_after_classify(state: AfterSaleWorkflowState) -> str:
        """根据分类结果决定继续查订单还是停在澄清边界。"""
        if state.get("order_id") and state.get("workflow_type") != "unknown":
            return "load_order"
        return "stop_before_submission"

    @staticmethod
    def _route_after_order(state: AfterSaleWorkflowState) -> str:
        """根据订单事实是否存在决定继续查物流还是停止流程。"""
        if state.get("order") is None:
            return "stop_before_submission"
        return "load_logistics"

    @staticmethod
    def _complete(state: AfterSaleWorkflowState, node: str, updates: dict[str, Any] | None = None) -> dict[str, Any]:
        """统一追加节点历史和当前节点，保证 workflow 状态可追踪。"""
        log_guhe_event("WORKFLOW_NODE", "LangGraph 节点执行完成", observe=True, node=node, workflow_id=state.get("workflow_id"))
        return {
            **(updates or {}),
            "current_node": node,
            "node_history": [*state.get("node_history", []), node],
        }

WORKFLOW = AfterSaleWorkflow()
