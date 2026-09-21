"""Step 19：Agent 编排层。每一步只在这里串起已学到的模块能力。"""

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
from models.clarification_planner import ClarificationPlannerModelClient


def is_stable_knowledge_request(user_message: str) -> bool:
    """识别不需要实时业务工具的稳定知识问题。"""
    stable_terms = ["活动", "优惠", "券", "满减", "折扣", "叠加", "售后规则", "发货规则"]
    realtime_terms = ["订单", "物流", "快递", "退款进度", "库存", "价格", "多少钱"]
    return any(term in user_message for term in stable_terms) and not any(term in user_message for term in realtime_terms)


def run_stable_rag(request: ChatRequest) -> tuple[str, Intent, list[Citation], dict[str, Any]]:
    """稳定知识问题继续走 RAG，不进入工具澄清模型。"""
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


def build_answer(intent: Intent, clarification: ClarificationRequest | None, observation: ToolObservation | None) -> str:
    """根据澄清状态或工具结果组织用户可读回答。"""
    if clarification:
        if clarification.candidates:
            choices = "；".join(f"{candidate.value}（{candidate.hint}）" for candidate in clarification.candidates)
            return f"{clarification.message} 我看到你名下有这些候选订单：{choices}。"
        return f"{clarification.message} 我现在没有足够信息调用工具。"
    if observation and observation.status == "success":
        return f"我通过工具查到：{observation.summary}"
    if observation:
        return f"这次工具没有查到可用事实：{observation.summary}"
    if intent in {"order_query", "refund_status_query"}:
        return "我需要订单号才能查询实时订单事实。"
    return "我没有识别到需要调用的实时业务工具。"

class Step19Agent:
    """LLM 澄清规划版 Agent。"""

    def __init__(self, *, planner_client: Any | None = None) -> None:
        """执行 __init__ 对应的项目逻辑。"""
        self._message_count_by_session: dict[str, int] = {}
        self._planner_client = planner_client or ClarificationPlannerModelClient()

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
                reasoning_summary=[
                    "稳定知识问题沿用 Hybrid RAG 和 citations。",
                    "Step 19 新增的 LLM 澄清规划只接管实时业务工具参数。",
                    "活动规则不需要为了展示 Tool Clarification 而绕进工具路径。",
                ],
                session_state={
                    "agent_version": "step-19-tool-clarification",
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
                        "clarification_plan": None,
                        "clarification": None,
                        "planned_action": None,
                        "observation": None,
                        "skip_reason": "stable_knowledge_routed_to_rag",
                    },
                    "next_gap": "工具能做前后澄清，但成功工具结果还没有做统一 ToolResult 与 Observation 压缩。",
                },
            )
        try:
            clarification_plan = enrich_plan_with_runtime_context(
                request,
                self._planner_client.plan_clarification(request, TOOL_SPECS),
            )
            log_guhe_event("CLARIFICATION_PLANNED", "模型已生成工具澄清计划", observe=True, intent=clarification_plan.intent, tool_name=clarification_plan.tool_name, missing_required=clarification_plan.missing_required)
        except Exception as exc:
            return self._build_planner_error_response(request, message_count, exc)

        clarification = pre_tool_clarification(request, clarification_plan)
        action = None if clarification else plan_tool_action(clarification_plan)
        log_guhe_event("TOOL_PRECHECK", "工具调用前澄清与动作规划完成", observe=True, needs_clarification=clarification is not None, tool_name=getattr(action, "tool_name", None))
        observation = execute_tool_action(action, request) if action else None
        if observation:
            log_guhe_event("TOOL_OBSERVATION", "工具执行结果已转为 Observation", observe=True, tool_name=action.tool_name, status=observation.status, observation_summary=observation.summary)
        if not clarification:
            clarification = post_tool_clarification(action, observation)
            log_guhe_event("TOOL_POSTCHECK", "工具调用后澄清判断完成", observe=True, needs_clarification=clarification is not None)
        tool_calls = [ToolCallRecord(action=action, observation=observation)] if action and observation else []
        deterministic_answer = build_answer(clarification_plan.intent, clarification, observation)
        model_answer = compose_grounded_answer(
            user_message=request.user_message,
            deterministic_answer=deterministic_answer,
            facts={
                "clarification_plan": clarification_plan,
                "clarification": clarification,
                "planned_action": action,
                "observation": observation,
                "runtime_context": public_runtime_context(request),
            },
            skip_model=clarification is not None,
            skip_reason="clarification_required",
        )

        session_state = {
            "agent_version": "step-19-tool-clarification",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "tool_calling": {
                "available_tools": [spec.model_dump() for spec in TOOL_SPECS.values()],
                "clarification_plan": clarification_plan.model_dump(),
                "clarification": clarification.model_dump() if clarification else None,
                "planned_action": action.model_dump() if action else None,
                "observation": observation.model_dump() if observation else None,
                "post_tool_clarification": (
                    clarification.model_dump()
                    if action and action.tool_name == "search_current_user_orders" and clarification
                    else None
                ),
            },
            "model_answer": model_answer.model_dump(),
            "next_gap": "工具能做前后澄清，但成功工具结果还没有做统一 ToolResult 与 Observation 压缩。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=model_answer.answer,
            intent=clarification_plan.intent,
            citations=[],
            tool_calls=tool_calls,
            clarification=clarification,
            reasoning_summary=[
                f"LLM 先输出 ClarificationPlan：intent={clarification_plan.intent}，tool={clarification_plan.tool_name or 'none'}。",
                "后端按 ToolSpec 重新计算缺失字段，模型输出不能绕过必填参数校验。",
                "如果候选查询返回多笔订单，仍然让用户确认，不能让 LLM 自动选择目标订单。",
            ],
            session_state=session_state,
        )

    def _build_planner_error_response(
        self,
        request: ChatRequest,
        message_count: int,
        error: Exception,
    ) -> ChatResponse:
        """构造澄清规划失败时的安全响应。"""
        session_state = {
            "agent_version": "step-19-tool-clarification",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "tool_calling": {
                "available_tools": [spec.model_dump() for spec in TOOL_SPECS.values()],
                "clarification_plan": None,
                "clarification": None,
                "planned_action": None,
                "observation": None,
                "post_tool_clarification": None,
                "planner_error": {
                    "source": "llm",
                    "fallback": "disabled",
                    "message": str(error),
                },
            },
            "model_answer": {
                "used_model": False,
                "model_name": None,
                "fallback_reason": "planner_unavailable",
                "source": "clarification_planner_error",
            },
            "next_gap": "模型规划不可用时，本步不会回退到规则抽取；后续项目再引入降级和治理。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer="当前 LLM 澄清规划不可用，系统没有回退到本地规则，也不会在参数不明时调用工具。请检查模型配置后重试。",
            intent="unknown",
            citations=[],
            tool_calls=[],
            clarification=None,
            reasoning_summary=[
                "Step 19 现在要求 LLM 先生成 ClarificationPlan。",
                "本轮模型规划失败，因此没有生成 Tool Action，也没有调用业务工具。",
                "这里刻意不回退到旧规则路径，避免把“模型澄清机制”讲成规则兜底。",
            ],
            session_state=session_state,
        )

def health() -> dict[str, str]:
    """返回当前步程后端健康状态。"""
    return {"status": "ok", "step": "19"}

def capabilities() -> dict[str, Any]:
    """返回当前步程的能力开关。"""
    return load_agent_capabilities()

def chat(request: ChatRequest) -> ChatResponse:
    """处理聊天请求并交给项目 Agent 编排。"""
    return agent.chat(request)
