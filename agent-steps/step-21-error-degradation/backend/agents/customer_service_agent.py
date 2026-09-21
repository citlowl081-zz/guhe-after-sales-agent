"""Step 21：Agent 编排层。每一步只在这里串起已学到的模块能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from typing import Any

from api.schemas import *
from config.settings import load_guhe_env
from models.llm_client import generate_stable_rag_answer
from rag.hybrid_retrieval import is_low_confidence, retrieve_knowledge
from rag.index_cache import get_knowledge_index
from rag.planning import classify_intent as classify_rag_intent
from rag.planning import pre_retrieval_plan
from rag.prompting import build_citations, render_rag_messages
from tools.runtime_context import *
from tools.contracts import *
from tools.planning import *
from tools.tool_runtime import *
from observability.observation import *
from degradation.fallbacks import *


def is_stable_knowledge_request(user_message: str) -> bool:
    """识别不需要实时业务工具和降级重试的稳定知识问题。"""
    stable_terms = ["活动", "优惠", "券", "满减", "折扣", "叠加", "售后规则", "发货规则"]
    realtime_terms = ["订单", "物流", "快递", "退款进度", "库存", "价格", "多少钱"]
    return any(term in user_message for term in stable_terms) and not any(term in user_message for term in realtime_terms)


def run_stable_rag(request: ChatRequest) -> tuple[str, Intent, list[Citation], dict[str, Any]]:
    """稳定知识问题继续走 RAG；工具错误降级只处理实时工具链路。"""
    intent = classify_rag_intent(request.user_message)
    plan = pre_retrieval_plan(request, intent)
    index = get_knowledge_index()
    hits, retrieval_debug = retrieve_knowledge(plan, index)
    log_guhe_event("RAG_RETRIEVED", "稳定知识检索完成", observe=True, candidate_count=len(hits))
    reliable_hits = [] if is_low_confidence(hits) else hits
    citations = build_citations(reliable_hits)
    if reliable_hits:
        model_answer = generate_stable_rag_answer(render_rag_messages(request, plan, reliable_hits, index))
        answer = model_answer.answer
        model_answer_state = model_answer.model_dump()
    else:
        answer = "这个问题没有找到足够可靠的古河商城规则依据，不能直接给出结论。"
        model_answer_state = {
            "used_model": False,
            "model_name": None,
            "fallback_reason": "low_confidence_no_model_answer",
            "source": "stable_rag_guardrail",
        }
    return answer, intent, citations, {
        "mode": "hybrid_rag_with_index_cache",
        "index_version": index.version,
        "plan": plan.model_dump(),
        "retrieved_count": len(hits),
        "citation_count": len(citations),
        "low_confidence": not bool(reliable_hits),
        "retrieval_debug": retrieval_debug,
        "model_answer": model_answer_state,
    }


class Step21Agent:
    """错误分类与降级版 Agent。"""

    def __init__(self) -> None:
        """执行 __init__ 对应的项目逻辑。"""
        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给项目 Agent 编排。"""
        load_guhe_env()
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        if is_stable_knowledge_request(request.user_message):
            answer, intent, citations, rag_state = run_stable_rag(request)
            degradation = DegradationState(degraded=False, error_category="none")
            return ChatResponse(
                session_id=request.session_id,
                answer=answer,
                intent=intent,
                citations=citations,
                tool_calls=[],
                clarification=None,
                next_action="answer_user",
                risk_level="low",
                needs_human_approval=False,
                degraded=False,
                reasoning_summary=[
                    "稳定知识问题沿用 Hybrid RAG 和 citations。",
                    "Step 21 新增的错误分类与降级只处理实时工具、模型回答和高风险动作。",
                    "活动规则命中 RAG 时不制造工具超时或高风险降级事件。",
                ],
                session_state={
                    "agent_version": "step-21-error-degradation",
                    "message_count": message_count,
                    "runtime_context": {
                        "user_id": request.runtime_user_id,
                        "nickname": request.runtime_nickname,
                        "member_level": request.runtime_member_level,
                        "risk_level": request.runtime_risk_level,
                        "page_context": public_runtime_context(request),
                    },
                    "rag": rag_state,
                    "tool_calling": {
                        "tool_specs": [spec.model_dump() for spec in TOOL_SPECS.values()],
                        "clarification": None,
                        "planned_action": None,
                        "observation": None,
                        "skip_reason": "stable_knowledge_routed_to_rag",
                    },
                    "degradation": degradation.model_dump(),
                    "next_gap": "错误降级能兜住系统不稳，但商品咨询还需要把实时库存价格和稳定知识一起组织成答案。",
                },
            )
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别实时业务意图", observe=True, intent=intent)
        risk_level = classify_risk(intent, request.user_message)

        if risk_level == "high":
            degradation = DegradationState(degraded=True, error_category="high_risk_write_blocked", fallback_message=fallback_answer("high_risk_write_blocked"))
            session_state = self._session_state(request, message_count, None, None, None, degradation)
            return ChatResponse(
                session_id=request.session_id,
                answer=degradation.fallback_message or "",
                intent=intent,
                citations=[],
                tool_calls=[],
                next_action="transfer_to_human",
                risk_level=risk_level,
                needs_human_approval=True,
                degraded=True,
                reasoning_summary=["高风险写操作不能由模型或普通工具直接执行。", "当前只返回转人工口径，不启动审批流程。"],
                session_state=session_state,
            )

        clarification = pre_tool_clarification(request, intent)
        action = None if clarification else plan_tool_action(request, intent)
        log_guhe_event("TOOL_PLANNED", "工具动作与前置澄清已确定", observe=True, tool_name=getattr(action, "tool_name", None), needs_clarification=clarification is not None)
        tool_result = execute_tool_action(action, request) if action else None
        if tool_result:
            log_guhe_event("TOOL_RESULT", "工具调用已结束", observe=True, status=tool_result.status, attempts=tool_result.attempts, error_category=tool_result.error_category)
        observation = build_observation(tool_result) if tool_result else None
        if observation:
            log_guhe_event("OBSERVATION_BUILT", "工具结果已治理为 Observation", observe=True, status=observation.status, next_action=observation.next_action)
        degradation = DegradationState(degraded=False, error_category="none")
        answer = "我没有识别到需要调用的实时业务工具。"
        if clarification:
            answer = f"{clarification.message} " + "；".join(f"{candidate.value}（{candidate.hint}）" for candidate in clarification.candidates)
        elif observation:
            if observation.status == "error" and observation.error_category == "timeout":
                degradation = DegradationState(
                    degraded=True,
                    error_category="timeout",
                    retry_count=(tool_result.attempts - 1) if tool_result else 0,
                    fallback_message=fallback_answer("timeout"),
                )
                log_guhe_event("DEGRADATION_APPLIED", "工具超时，已进入可解释降级", observe=True, error_category=degradation.error_category, retry_count=degradation.retry_count)
                answer = degradation.fallback_message or ""
            else:
                try:
                    answer = compose_model_answer(observation, request.user_message)
                except ModelServiceError:
                    degradation = DegradationState(degraded=True, error_category="model_unavailable", fallback_message=fallback_answer("model_unavailable"))
                    answer = f"{degradation.fallback_message} {observation.summary}"

        next_action: NextAction = "ask_clarification" if clarification else observation.next_action if observation else "fallback_answer"
        if degradation.degraded and degradation.error_category in {"timeout", "model_unavailable"}:
            next_action = "fallback_answer"
        tool_calls = [ToolCallRecord(action=action, observation=observation, attempts=tool_result.attempts)] if action and observation and tool_result else []
        session_state = self._session_state(request, message_count, clarification, action, observation, degradation)
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=[],
            tool_calls=tool_calls,
            clarification=clarification,
            next_action=next_action,
            risk_level=risk_level,
            needs_human_approval=False,
            degraded=degradation.degraded,
            reasoning_summary=[
                "后端先区分只读查询和高风险写操作。",
                "只读工具超时可以重试一次，仍失败就降级，不编造业务事实。",
                "高风险退款、取消和补偿只给转人工口径，不在本步实现审批流。",
            ],
            session_state=session_state,
        )

    def _session_state(
        self,
        request: ChatRequest,
        message_count: int,
        clarification: ClarificationRequest | None,
        action: ToolAction | None,
        observation: Observation | None,
        degradation: DegradationState,
    ) -> dict[str, Any]:
        """整理观察台需要展示的项目状态。"""
        return {
            "agent_version": "step-21-error-degradation",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "tool_calling": {
                "tool_specs": [spec.model_dump() for spec in TOOL_SPECS.values()],
                "clarification": clarification.model_dump() if clarification else None,
                "planned_action": action.model_dump() if action else None,
                "observation": observation.model_dump() if observation else None,
            },
            "degradation": degradation.model_dump(),
            "next_gap": "错误降级能兜住系统不稳，但商品咨询还需要把实时库存价格和稳定知识一起组织成答案。",
        }

def health() -> dict[str, str]:
    """返回当前步程后端健康状态。"""
    return {"status": "ok", "step": "21"}

def capabilities() -> dict[str, Any]:
    """返回当前步程的能力开关。"""
    return load_agent_capabilities()

def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给项目 Agent 编排。"""
    return agent.chat(request)
