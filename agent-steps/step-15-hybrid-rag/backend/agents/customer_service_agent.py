"""Step 15 Hybrid RAG 版 Agent。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from api.schemas import ChatRequest, ChatResponse
from config.settings import FINAL_TOP_K, KEYWORD_TOP_K, VECTOR_TOP_K
from models.llm_client import generate_stable_rag_answer
from rag.hybrid_retrieval import is_low_confidence, retrieve_knowledge
from rag.planning import classify_intent, pre_retrieval_plan
from rag.prompting import build_citations, render_rag_messages


class Step15Agent:
    """Hybrid RAG 版 Agent。"""

    def __init__(self) -> None:
        """初始化最小会话状态。"""

        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """执行一次带检索前计划的 Hybrid RAG 问答。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent)
        plan = pre_retrieval_plan(request, intent)
        log_guhe_event("RETRIEVAL_PLAN_CREATED", "检索计划生成完成", observe=True, scene=plan.scene, rewritten_query=plan.rewritten_query, allowed_topics=plan.allowed_topics, keyword_terms=plan.keyword_terms)
        hits, retrieval_debug = retrieve_knowledge(plan)
        log_guhe_event("HYBRID_RETRIEVED", "Hybrid RAG检索完成", observe=True, hit_count=len(hits), vector_chunk_ids=retrieval_debug.get("vector_chunk_ids"), keyword_chunk_ids=retrieval_debug.get("keyword_chunk_ids"), source_scores=retrieval_debug.get("source_scores"))
        low_confidence = is_low_confidence(hits)
        log_guhe_event("CONFIDENCE_DECIDED", "混合检索置信度判断完成", low_confidence=low_confidence, top_score=hits[0].score if hits else 0.0)
        reliable_hits = [] if low_confidence else hits
        citations = build_citations(reliable_hits)
        log_guhe_event("CITATIONS_BUILT", "可靠引用生成完成", citation_count=len(citations))
        if reliable_hits:
            model_answer = generate_stable_rag_answer(render_rag_messages(request, plan, reliable_hits))
            answer = model_answer.answer
            model_answer_state = model_answer.model_dump()
        else:
            answer = "我现在没有找到足够可靠的古河商城规则依据，不能直接给出结论。请补充商品、活动页或售后条件后再核验。"
            model_answer_state = {
                "used_model": False,
                "model_name": None,
                "fallback_reason": "low_confidence_no_model_answer",
                "source": "stable_rag_guardrail",
            }

        reasoning_summary = [
            "后端先做 pre-retrieval，判断问题属于哪类知识场景。",
            f"本轮场景是 {plan.scene}，再组合向量召回和关键词召回。",
            "Step 15 只升级知识检索质量，不接入订单、物流、库存或退款进度工具。",
        ]
        session_state = {
            "agent_version": "step-15-hybrid-rag",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "rag": {
                "mode": "hybrid_rag",
                "plan": plan.model_dump(),
                "vector_top_k": VECTOR_TOP_K,
                "keyword_top_k": KEYWORD_TOP_K,
                "final_top_k": FINAL_TOP_K,
                "matched_chunk_ids": [hit.chunk.chunk_id for hit in reliable_hits],
                "confidence_level": "low" if low_confidence else "high",
                "citation_count": len(citations),
                "model_answer": model_answer_state,
                **retrieval_debug,
            },
            "next_gap": "Hybrid RAG 让召回更稳，但大促前规则暴涨时，索引重建、增量更新和缓存边界会成为新的压力。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
