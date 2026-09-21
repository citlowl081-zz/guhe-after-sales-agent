"""Step 20：Agent 编排层。每一步只在这里串起已学到的模块能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from typing import Any

from api.schemas import *
from config.settings import load_guhe_env
from models.answer_client import compose_grounded_answer
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


def is_stable_knowledge_request(user_message: str) -> bool:
    """识别不需要实时业务工具的稳定知识问题。"""
    stable_terms = ["活动", "优惠", "券", "满减", "折扣", "叠加", "售后规则", "发货规则"]
    realtime_terms = ["订单", "物流", "快递", "退款进度", "库存", "价格", "多少钱"]
    return any(term in user_message for term in stable_terms) and not any(term in user_message for term in realtime_terms)


def run_stable_rag(request: ChatRequest) -> tuple[str, Intent, list[Citation], dict[str, Any]]:
    """稳定知识问题继续走 RAG，不进入工具 Observation 路径。"""
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


def build_answer(clarification: ClarificationRequest | None, observation: Observation | None) -> str:
    """根据澄清状态或工具结果组织用户可读回答。"""
    if clarification:
        choices = "；".join(f"{candidate.value}（{candidate.hint}）" for candidate in clarification.candidates)
        return f"{clarification.message} {choices}"
    if observation and observation.status == "success":
        return f"我把工具结果压缩后再回答：{observation.summary}"
    if observation:
        return f"工具没有返回可用事实：{observation.summary}"
    return "我没有识别到需要调用的实时业务工具。"

class Step20Agent:
    """ToolResult 与 Observation 版 Agent。"""

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
            return ChatResponse(
                session_id=request.session_id,
                answer=answer,
                intent=intent,
                citations=citations,
                tool_calls=[],
                clarification=None,
                next_action="answer_user",
                reasoning_summary=[
                    "稳定知识问题沿用 Hybrid RAG 和 citations。",
                    "Step 20 新增的 ToolResult/Observation 只压缩实时工具结果。",
                    "RAG 命中不伪装成工具 Observation，避免混淆事实来源。",
                ],
                session_state={
                    "agent_version": "step-20-tool-result-observation",
                    "message_count": message_count,
                    "runtime_context": {
                        "user_id": request.runtime_user_id,
                        "nickname": request.runtime_nickname,
                        "member_level": request.runtime_member_level,
                        "risk_level": request.runtime_risk_level,
                        "page_context": public_runtime_context(request),
                    },
                    "rag": rag_state,
                    "model_answer": rag_state["model_answer"],
                    "tool_calling": {
                        "clarification": None,
                        "planned_action": None,
                        "tool_result_status": None,
                        "raw_payload_keys": [],
                        "observation": None,
                        "skip_reason": "stable_knowledge_routed_to_rag",
                    },
                    "next_gap": "Observation 已经能压缩工具结果，但工具超时、模型服务不可用和高风险动作还需要错误分类与降级策略。",
                },
            )
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "已识别实时业务意图", observe=True, intent=intent)
        clarification = pre_tool_clarification(request, intent)
        action = None if clarification else plan_tool_action(request, intent)
        log_guhe_event("TOOL_PLANNED", "工具动作与前置澄清已确定", observe=True, tool_name=getattr(action, "tool_name", None), needs_clarification=clarification is not None)
        tool_result = execute_tool_action(action, request) if action else None
        if tool_result:
            log_guhe_event("TOOL_RESULT", "工具执行结束，原始结果仅进入治理层", observe=True, status=tool_result.status, attempts=getattr(tool_result, "attempts", 1))
        observation = build_observation(tool_result) if tool_result else None
        if observation:
            log_guhe_event("OBSERVATION_BUILT", "ToolResult 已压缩为安全 Observation", observe=True, status=observation.status, next_action=observation.next_action, observation_summary=observation.summary)
        next_action: NextAction = "ask_clarification" if clarification else observation.next_action if observation else "fallback_answer"
        log_guhe_event("NEXT_ACTION", "已根据 Observation 决定下一步", observe=True, next_action=next_action)
        tool_calls = [ToolCallRecord(action=action, observation=observation)] if action and observation else []
        deterministic_answer = build_answer(clarification, observation)
        skip_reason = "clarification_required" if clarification else "missing_observation"
        model_answer = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=deterministic_answer,
            facts={
                "clarification": clarification,
                "planned_action": action,
                "tool_result_status": tool_result.status if tool_result else None,
                "observation": observation,
                "next_action": next_action,
                "runtime_context": public_runtime_context(request),
            },
            skip_model=clarification is not None or observation is None,
            skip_reason=skip_reason,
        )

        session_state = {
            "agent_version": "step-20-tool-result-observation",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "tool_calling": {
                "clarification": clarification.model_dump() if clarification else None,
                "planned_action": action.model_dump() if action else None,
                "tool_result_status": tool_result.status if tool_result else None,
                "raw_payload_keys": list(tool_result.raw_payload.keys()) if tool_result else [],
                "observation": observation.model_dump() if observation else None,
            },
            "model_answer": model_answer.model_dump(),
            "next_gap": "Observation 已经能压缩工具结果，但工具超时、模型服务不可用和高风险动作还需要错误分类与降级策略。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=model_answer.answer,
            intent=intent,
            citations=[],
            tool_calls=tool_calls,
            clarification=clarification,
            next_action=next_action,
            reasoning_summary=[
                "工具先返回内部 ToolResult。",
                "后端把 ToolResult 压缩成给模型和用户都更安全的 Observation。",
                "Observation 只保留摘要、关键 facts、被省略字段和下一步动作。",
            ],
            session_state=session_state,
        )

def health() -> dict[str, str]:
    """返回当前步程后端健康状态。"""
    return {"status": "ok", "step": "20"}

def capabilities() -> dict[str, Any]:
    """返回当前步程的能力开关。"""
    return load_agent_capabilities()

def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给项目 Agent 编排。"""
    return agent.chat(request)
