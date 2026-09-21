"""Step 16 索引更新与 RAG 缓存版 Agent。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from api.schemas import ChatRequest, ChatResponse
from models.llm_client import generate_stable_rag_answer
from rag.hybrid_retrieval import is_low_confidence, retrieve_knowledge
from rag.index_cache import get_knowledge_index
from rag.planning import classify_intent, is_realtime_business_query, pre_retrieval_plan
from rag.prompting import build_citations, render_rag_messages


class Step16Agent:
    """索引更新与 RAG 缓存版 Agent。"""

    def __init__(self) -> None:
        """初始化最小会话状态。"""

        self._message_count_by_session: dict[str, int] = {}

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """执行一次带索引版本和检索缓存的 Hybrid RAG 问答。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent)
        plan = pre_retrieval_plan(request, intent)
        log_guhe_event("RETRIEVAL_PLAN_CREATED", "检索计划生成完成", observe=True, scene=plan.scene, rewritten_query=plan.rewritten_query, allowed_topics=plan.allowed_topics, keyword_terms=plan.keyword_terms)
        index = get_knowledge_index()
        log_guhe_event("KNOWLEDGE_INDEX_READY", "知识索引已就绪", observe=True, version=index.version, fingerprint=index.fingerprint, chunk_count=index.chunk_count)
        realtime_gap = is_realtime_business_query(request.user_message)
        log_guhe_event("REALTIME_QUERY_DECIDED", "实时业务问题判断完成", realtime_gap=realtime_gap)
        hits, retrieval_debug = retrieve_knowledge(plan, index)
        log_guhe_event("RAG_CACHE_DECIDED", "检索缓存判断完成", observe=True, cache_hit=retrieval_debug["cache"]["cache_hit"], cacheable=retrieval_debug["cache"]["cacheable"], cache_key=retrieval_debug["cache"]["cache_key"], reason=retrieval_debug["cache"]["reason"])
        log_guhe_event("HYBRID_RETRIEVED", "Hybrid RAG检索完成", hit_count=len(hits), vector_chunk_ids=retrieval_debug.get("vector_chunk_ids"), keyword_chunk_ids=retrieval_debug.get("keyword_chunk_ids"), source_scores=retrieval_debug.get("source_scores"))
        low_confidence = realtime_gap or is_low_confidence(hits)
        log_guhe_event("CONFIDENCE_DECIDED", "检索置信度判断完成", observe=True, low_confidence=low_confidence, realtime_gap=realtime_gap, top_score=hits[0].score if hits else 0.0)
        reliable_hits = [] if low_confidence else hits
        citations = build_citations(reliable_hits)
        log_guhe_event("CITATIONS_BUILT", "可靠引用生成完成", citation_count=len(citations))
        if realtime_gap:
            # Step 16 还没有业务事实服务；遇到实时状态问题先保守承认缺口，避免 RAG 缓存误答。
            answer = "知识库再强，也查不到正在路上的快递或某个订单的实时状态。这个问题需要接入业务工具后再查询。"
            model_answer_state = {
                "used_model": False,
                "model_name": None,
                "fallback_reason": "realtime_gap_no_model_answer",
                "source": "stable_rag_guardrail",
            }
        elif reliable_hits:
            model_answer = generate_stable_rag_answer(render_rag_messages(request, plan, reliable_hits, index))
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
            "后端先确认当前知识索引版本，再执行 Hybrid RAG。",
            f"本轮索引版本是 {index.version}，检索缓存命中为 {retrieval_debug['cache']['cache_hit']}。",
            "RAG 缓存只缓存稳定知识检索结果，不缓存最终回答，也不缓存实时业务问题。",
        ]
        session_state = {
            "agent_version": "step-16-index-cache",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "rag": {
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
                "realtime_gap": realtime_gap,
                "model_answer": model_answer_state,
                **retrieval_debug,
            },
            "next_gap": "知识库检索越来越稳，但用户开始问订单、物流、库存和退款进度。知识库再强，也查不到正在路上的快递，下一幕必须让 Agent 使用业务工具。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
