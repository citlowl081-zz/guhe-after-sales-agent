"""Step 17：Agent 编排层。每一步只在这里串起已学到的模块能力。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from api.schemas import ChatRequest, ChatResponse
from config.settings import load_guhe_env
from models.answer_client import compose_grounded_answer
from models.llm_client import generate_stable_rag_answer
from rag.hybrid_retrieval import is_low_confidence, retrieve_knowledge
from rag.index_cache import get_knowledge_index
from rag.planning import classify_intent as classify_rag_intent
from rag.planning import pre_retrieval_plan
from rag.prompting import build_citations, render_rag_messages
from services.business_facts import BusinessFactService
from tools.planning import classify_intent as classify_business_intent
from tools.planning import detect_business_fact_need
from tools.runtime_context import public_runtime_context


def model_answer_state(
    *,
    used_model: bool,
    model_name: str | None,
    fallback_reason: str | None,
    source: str,
) -> dict[str, object]:
    """统一公开给调试后台的最终回答来源结构。"""
    return {
        "used_model": used_model,
        "model_name": model_name,
        "fallback_reason": fallback_reason,
        "source": source,
    }


class Step17Agent:
    """实时业务事实版 Agent：用只读业务事实服务接住订单、物流和库存状态。"""

    def __init__(self) -> None:
        self._message_count_by_session: dict[str, int] = {}
        self._business_facts = BusinessFactService()

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """处理聊天请求并交给项目 Agent 编排。"""
        load_guhe_env()
        self._message_count_by_session[request.session_id] = self._message_count_by_session.get(request.session_id, 0) + 1
        message_count = self._message_count_by_session[request.session_id]
        business_intent = classify_business_intent(request.user_message)
        need = detect_business_fact_need(request)
        log_guhe_event("ROUTE_CLASSIFIED", "已区分稳定知识与实时业务事实", observe=True, intent=business_intent, requires_realtime=need.requires_realtime)
        fact_result = None
        citations = []
        rag_state = None
        model_answer = None

        if need.requires_realtime:
            intent = business_intent
            fact_result = self._business_facts.lookup(need, request)
            log_guhe_event("BUSINESS_FACT_LOOKUP", "实时业务事实查询完成", observe=True, found=bool(fact_result and fact_result.found), user_matched=getattr(fact_result, "user_matched", None))
            if fact_result and fact_result.found:
                deterministic_answer = f"我查到古河商城公司的实时业务事实：{fact_result.summary}"
            elif fact_result and not fact_result.user_matched:
                deterministic_answer = "这个订单不属于当前登录用户，我不能把别人的订单或物流信息拿来回答。"
            else:
                deterministic_answer = f"这个问题需要查询实时业务事实。{fact_result.summary if fact_result else '请补充订单号或商品信息。'}"
            fact_unavailable = not bool(fact_result and fact_result.found)
            fact_forbidden = bool(fact_result and not fact_result.user_matched)
            skip_model = fact_unavailable or fact_forbidden
            model_answer = compose_grounded_answer(
                user_message=request.user_message,
                deterministic_answer=deterministic_answer,
                facts={
                    "business_need": need.model_dump(),
                    "business_fact_result": fact_result.model_dump() if fact_result else None,
                    "runtime_context": public_runtime_context(request),
                },
                skip_model=skip_model,
                skip_reason="business_fact_unavailable_or_forbidden",
            )
            answer = model_answer.answer
        else:
            intent = classify_rag_intent(request.user_message)
            plan = pre_retrieval_plan(request, intent)
            index = get_knowledge_index()
            hits, retrieval_debug = retrieve_knowledge(plan, index)
            log_guhe_event("RAG_RETRIEVED", "稳定知识检索完成", observe=True, candidate_count=len(hits), index_version=index.version)
            low_confidence = is_low_confidence(hits)
            reliable_hits = [] if low_confidence else hits
            citations = build_citations(reliable_hits)
            log_guhe_event("RAG_ASSESSED", "检索质量与引用已确认", observe=True, low_confidence=low_confidence, citation_count=len(citations))
            if reliable_hits:
                stable_rag_answer = generate_stable_rag_answer(render_rag_messages(request, plan, reliable_hits, index))
                answer = stable_rag_answer.answer
                model_answer = stable_rag_answer
            else:
                answer = "这个问题没有命中实时业务事实，我也没有找到足够可靠的古河商城规则依据，不能直接给出结论。"
                model_answer = model_answer_state(
                    used_model=False,
                    model_name=None,
                    fallback_reason="low_confidence_no_model_answer",
                    source="stable_rag_guardrail",
                )
            rag_state = {
                "mode": "hybrid_rag_with_index_cache",
                "plan": plan.model_dump(),
                "index": {
                    "version": index.version,
                    "fingerprint": index.fingerprint,
                    "chunk_count": index.chunk_count,
                    "inverted_term_count": len(index.inverted_index),
                },
                "matched_chunk_ids": [hit.chunk.chunk_id for hit in reliable_hits],
                "confidence_level": "low" if low_confidence else "high",
                "citation_count": len(citations),
                **retrieval_debug,
            }

        reasoning_summary = [
            "后端先判断用户问的是稳定规则，还是订单、物流、库存这类实时业务事实。",
            "稳定规则继续走 Step 16 的 Hybrid RAG 和索引缓存，实时事实才转交业务事实服务。",
            "这一版重点是可信事实接入和身份边界，避免模型把实时状态当知识库答案猜。",
        ]
        session_state = {
            "agent_version": "step-17-realtime-business-facts",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": public_runtime_context(request),
            },
            "business_facts": {
                "mode": "direct_business_fact_service",
                "need": need.model_dump(),
                "result": fact_result.model_dump() if fact_result else None,
            },
            "rag": rag_state,
            "model_answer": model_answer.model_dump() if hasattr(model_answer, "model_dump") else model_answer,
            "next_gap": "当前边界是只读事实查询；写操作、复杂澄清和跨系统治理不在本步展开。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
