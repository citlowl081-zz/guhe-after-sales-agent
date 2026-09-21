"""Step 13 查询改写版 Agent。

Agent 保留用户原话，同时生成 rewritten_query 专门服务检索。
"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

from api.schemas import ChatRequest, ChatResponse, Intent
from config.settings import LOW_CONFIDENCE_THRESHOLD, RETRIEVAL_SCORE_THRESHOLD, TOP_K
from embeddings.client import EmbeddingClient, read_embedding_model_name
from models.llm_client import call_chat_model
from rag.prompting import build_citations, render_rag_messages
from rag.query_rewrite import normalize_query, rewrite_retrieval_query
from rag.retrieval import is_low_confidence, retrieve_knowledge


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


class Step13Agent:
    """查询改写版 Agent。"""

    def __init__(self, *, embedding_client: EmbeddingClient | None = None) -> None:
        """初始化最小会话状态。"""

        self._message_count_by_session: dict[str, int] = {}
        self._embedding_client = embedding_client

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """执行一次先改写再检索的 RAG 问答。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent)
        raw_hits = retrieve_knowledge(request.user_message, embedding_client=self._embedding_client)
        log_guhe_event("RAG_ORIGINAL_RETRIEVED", "原始问题检索完成", hit_count=len(raw_hits), top_chunk_id=raw_hits[0].chunk.chunk_id if raw_hits else None)
        rewrite = rewrite_retrieval_query(request, intent)
        log_guhe_event("QUERY_REWRITTEN", "检索问题改写完成", observe=True, applied=rewrite.applied, original_query=rewrite.original_query, rewritten_query=rewrite.rewritten_query)
        rewritten_hits = retrieve_knowledge(rewrite.rewritten_query, embedding_client=self._embedding_client)
        log_guhe_event("RAG_REWRITTEN_RETRIEVED", "改写问题检索完成", observe=True, hit_count=len(rewritten_hits), top_chunk_id=rewritten_hits[0].chunk.chunk_id if rewritten_hits else None)
        low_confidence = is_low_confidence(rewritten_hits)
        log_guhe_event("CONFIDENCE_DECIDED", "RAG置信度判断完成", low_confidence=low_confidence, threshold=LOW_CONFIDENCE_THRESHOLD, top_score=rewritten_hits[0].score if rewritten_hits else 0.0)
        reliable_hits = [] if low_confidence else rewritten_hits
        citations = build_citations(reliable_hits)
        log_guhe_event("CITATIONS_BUILT", "可靠引用生成完成", citation_count=len(citations))
        if reliable_hits:
            messages = render_rag_messages(request, rewrite, reliable_hits)
            answer = call_chat_model(messages)
        else:
            answer = "我现在没有找到足够可靠的古河商城规则依据，不能直接给出结论。请补充商品、活动页或售后条件后再核验。"

        top_score = rewritten_hits[0].score if rewritten_hits else 0.0
        reasoning_summary = [
            "后端先保留用户原话，再为检索单独生成 rewritten_query。",
            f"原始问题 top 命中是 {raw_hits[0].chunk.chunk_id if raw_hits else 'none'}，改写后 top 命中是 {rewritten_hits[0].chunk.chunk_id if rewritten_hits else 'none'}。",
            "Step 13 只改变检索问题，不改变用户原话，也不接入业务工具。",
        ]
        session_state = {
            "agent_version": "step-13-query-rewrite",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "rag": {
                "mode": "query_rewrite_vector_retrieval",
                "embedding_model": read_embedding_model_name(),
                "top_k": TOP_K,
                "score_threshold": RETRIEVAL_SCORE_THRESHOLD,
                "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
                "rewrite": rewrite.model_dump(),
                "raw_top_chunk_id": raw_hits[0].chunk.chunk_id if raw_hits else None,
                "matched_chunk_ids": [hit.chunk.chunk_id for hit in reliable_hits],
                "raw_retrieved_count": len(raw_hits),
                "retrieved_count": len(reliable_hits),
                "citation_count": len(citations),
                "confidence_level": "low" if low_confidence else "high",
                "top_score": top_score,
            },
            "next_gap": "查询改写能缓解口语化问题，但相似规则同时召回时，top-k 第一名仍然可能不是真正答案。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
