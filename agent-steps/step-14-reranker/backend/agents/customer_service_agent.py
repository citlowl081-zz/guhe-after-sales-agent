"""Step 14 Reranker 重排版 Agent。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from api.schemas import ChatRequest, ChatResponse, Intent
from config.settings import CANDIDATE_K, FINAL_TOP_K, RETRIEVAL_SCORE_THRESHOLD
from embeddings.client import EmbeddingClient, read_embedding_model_name
from models.llm_client import call_chat_model
from rag.prompting import build_citations, render_rag_messages
from rag.query_rewrite import normalize_query, rewrite_retrieval_query
from rag.reranker import rerank_candidates
from rag.retrieval import is_low_confidence, merge_candidates, retrieve_candidates


def classify_intent(user_message: str) -> Intent:
    """用归一化后的文本识别粗意图。"""

    message = normalize_query(user_message)
    if any(word in message for word in ["投诉", "举报", "赔偿", "曝光", "315"]):
        return "complaint"
    if any(word in message for word in ["退款", "退货", "取消订单", "坏了", "无法开机", "质量问题", "无理由"]):
        return "refund_request"
    if any(word in message for word in ["物流", "快递", "发货", "到哪", "运单"]):
        return "order_query"
    if any(word in message for word in ["优惠", "活动", "会员价", "券", "满减", "折扣"]):
        return "promotion_consult"
    if any(word in message for word in ["耳机", "充电器", "音箱", "推荐", "哪个好"]):
        return "product_consult"
    return "unknown"


class Step14Agent:
    """Reranker 重排版 Agent。"""

    def __init__(self, *, embedding_client: EmbeddingClient | None = None) -> None:
        """初始化最小会话状态。"""

        self._message_count_by_session: dict[str, int] = {}
        self._embedding_client = embedding_client

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """执行一次召回候选后再重排的 RAG 问答。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent)
        rewrite = rewrite_retrieval_query(request, intent)
        log_guhe_event("QUERY_REWRITTEN", "检索问题改写完成", observe=True, applied=rewrite.applied, rewritten_query=rewrite.rewritten_query)
        original_candidates = retrieve_candidates(request.user_message, embedding_client=self._embedding_client)
        rewritten_candidates = retrieve_candidates(rewrite.rewritten_query, embedding_client=self._embedding_client)
        candidates = merge_candidates(original_candidates, rewritten_candidates)
        log_guhe_event("RAG_CANDIDATES_MERGED", "多路候选合并完成", candidate_count=len(candidates), chunk_ids=[hit.chunk.chunk_id for hit in candidates])
        rerank_outcome = rerank_candidates(rewrite.rewritten_query, candidates)
        log_guhe_event("RERANK_COMPLETED", "候选重排完成", observe=True, mode=rerank_outcome.mode, candidate_count=len(candidates), scores=[hit.score for hit in rerank_outcome.hits])
        reranked = rerank_outcome.hits
        low_confidence = is_low_confidence(reranked)
        log_guhe_event("CONFIDENCE_DECIDED", "重排结果置信度判断完成", low_confidence=low_confidence, top_score=reranked[0].score if reranked else 0.0)
        reliable_hits = [] if low_confidence else reranked[:FINAL_TOP_K]
        citations = build_citations(reliable_hits)
        log_guhe_event("CITATIONS_BUILT", "最终引用生成完成", citation_count=len(citations))
        if reliable_hits:
            answer = call_chat_model(render_rag_messages(request, rewrite, reliable_hits))
        else:
            answer = "我现在没有找到足够可靠的古河商城规则依据，不能直接给出结论。请补充商品、活动页或售后条件后再核验。"

        reasoning_summary = [
            f"后端先召回候选片段，再用 {rerank_outcome.mode} reranker 重新排序。",
            f"初步召回第一名是 {original_candidates[0].chunk.chunk_id if original_candidates else 'none'}，重排后第一名是 {reranked[0].chunk.chunk_id if reranked else 'none'}。",
            "Step 14 只重排知识片段，不接入业务工具，也不把重排过程包装成完整 Eval 平台。",
        ]
        session_state = {
            "agent_version": "step-14-reranker",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "rag": {
                "mode": "query_rewrite_with_reranker",
                "embedding_model": read_embedding_model_name(),
                "score_threshold": RETRIEVAL_SCORE_THRESHOLD,
                "rerank_mode": rerank_outcome.mode,
                "rerank_model": rerank_outcome.model,
                "rerank_error": rerank_outcome.error,
                "candidate_k": CANDIDATE_K,
                "final_top_k": FINAL_TOP_K,
                "rewrite": rewrite.model_dump(),
                "initial_top_chunk_id": original_candidates[0].chunk.chunk_id if original_candidates else None,
                "candidate_chunk_ids": [hit.chunk.chunk_id for hit in candidates],
                "reranked_chunk_ids": [hit.chunk.chunk_id for hit in reranked],
                "selected_chunk_ids": [hit.chunk.chunk_id for hit in reliable_hits],
                "rerank_reasons": {hit.chunk.chunk_id: hit.rerank_reasons for hit in reranked},
                "scores": {
                    hit.chunk.chunk_id: {"vector": hit.vector_score, "rerank": hit.rerank_score, "final": hit.score}
                    for hit in reranked
                },
                "confidence_level": "low" if low_confidence else "high",
                "citation_count": len(citations),
            },
            "next_gap": "重排能改善相似规则排序，但长尾售后词、规则编号和精确关键词只靠向量仍然容易漏召回。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
