"""Step 12 低置信兜底 Agent。

这一步在 citations 前加一层质量判断：命中分数不够时，不返回引用，也不硬答。
"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse, Citation, Intent, KnowledgeHit
from config.settings import LOW_CONFIDENCE_THRESHOLD, RETRIEVAL_SCORE_THRESHOLD, TOP_K, load_guhe_env
from cost.observer import build_cost_summary
from embeddings.client import EmbeddingClient, read_embedding_model_name
from models.llm_client import call_chat_model
from rag.quality import is_low_confidence, run_rag_quality_check
from rag.retrieval import retrieve_knowledge


def build_citations(hits: list[KnowledgeHit]) -> list[Citation]:
    """把可靠命中转换成 citations。"""

    # Step 12 继续要求 citations 只能来自可靠命中，不能由模型临场编。
    return [
        Citation(
            citation_id=f"C{index}",
            source_title=hit.chunk.title,
            source_path=hit.chunk.source_path,
            chunk_id=hit.chunk.chunk_id,
            score=hit.score,
            snippet=hit.chunk.text,
        )
        for index, hit in enumerate(hits, start=1)
    ]


def build_fallback_answer() -> str:
    """低置信或无可靠依据时生成固定兜底回答。"""

    return (
        "我现在没有找到足够可靠的古河商城规则依据，不能直接给出结论。"
        "请你补充订单状态、商品名称或活动页面信息；如果问题涉及售后争议，我会建议转人工继续核验。"
    )


def build_general_chat_answer() -> str:
    """普通客服寒暄不需要触发低置信兜底。"""

    return (
        "你好，我是古河商城公司的客服 Agent。你可以直接描述商品、活动、发货、物流、发票或售后问题；"
        "涉及具体规则时，我会优先依据当前知识库回答，证据不足时会提示你补充信息。"
    )


def render_quality_checked_rag_messages(
    request: ChatRequest,
    intent: Intent,
    reliable_hits: list[KnowledgeHit],
    low_confidence: bool,
) -> list[dict[str, str]]:
    """把质量检查后的 RAG 证据渲染成模型输入。"""
    context = "\n\n".join(f"[{hit.chunk.chunk_id} | score={hit.score}]\n{hit.chunk.text}" for hit in reliable_hits)
    if not context:
        context = "当前问题没有足够高置信命中。回答时要承认缺少可靠依据。"
    return [
        {
            "role": "system",
            "content": (
                "你是古河商城公司的客服 Agent。只能依据高置信 RAG 命中回答；"
                "低置信时要拒绝编造，并引导补充信息或转人工。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户原话：{request.user_message}\n"
                f"粗意图：{intent}\n"
                f"低置信：{low_confidence}\n"
                f"当前用户：{request.runtime_user_id}，会员等级：{request.runtime_member_level or '未提供'}\n"
                f"高置信命中知识：\n{context}"
            ),
        },
    ]


def render_general_chat_messages(request: ChatRequest, intent: Intent) -> list[dict[str, str]]:
    """记录普通客服回答的输入边界，避免把寒暄误当作低置信 RAG。"""

    return [
        {
            "role": "system",
            "content": "你是古河商城公司的客服 Agent。普通寒暄可以直接接住，业务规则结论必须有知识依据。",
        },
        {
            "role": "user",
            "content": (
                f"用户原话：{request.user_message}\n"
                f"粗意图：{intent}\n"
                "当前分支：普通客服寒暄，不需要 citations。"
            ),
        },
    ]


def classify_intent(user_message: str, hits: list[KnowledgeHit]) -> Intent:
    """按用户文本和可靠命中推断当前粗意图。"""

    message = user_message.strip().lower()
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
    if any(word in message for word in ["你好", "您好", "在吗", "谢谢", "辛苦", "你是谁", "能做什么"]):
        return "general_chat"
    if hits:
        return "product_consult"
    return "unknown"


class Step12Agent:
    """RAG 质量评测与低置信兜底版 Agent。"""

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient | None = None,
        chat_http_client: httpx.Client | None = None,
        chat_api_key: str | None = None,
        chat_base_url: str | None = None,
        chat_model_name: str | None = None,
    ) -> None:
        """保存 embedding 客户端，并初始化最小会话状态。"""

        self._message_count_by_session: dict[str, int] = {}
        self._embedding_client = embedding_client
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """执行一次带低置信判断的 RAG 问答。"""

        load_guhe_env()
        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        raw_hits = retrieve_knowledge(request.user_message, embedding_client=self._embedding_client)
        log_guhe_event("RAG_RETRIEVED", "候选知识检索完成", observe=True, hit_count=len(raw_hits), scores={hit.chunk.chunk_id: hit.score for hit in raw_hits})
        low_confidence = is_low_confidence(raw_hits)
        log_guhe_event("CONFIDENCE_DECIDED", "RAG置信度判断完成", observe=True, low_confidence=low_confidence, threshold=LOW_CONFIDENCE_THRESHOLD, top_score=raw_hits[0].score if raw_hits else 0.0)
        reliable_hits = [] if low_confidence else raw_hits
        citations = build_citations(reliable_hits)
        log_guhe_event("CITATIONS_BUILT", "可靠引用生成完成", citation_count=len(citations), chunk_ids=[hit.chunk.chunk_id for hit in reliable_hits])
        intent = classify_intent(request.user_message, reliable_hits)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent)
        if reliable_hits:
            messages = render_quality_checked_rag_messages(request, intent, reliable_hits, low_confidence)
            answer = call_chat_model(
                messages,
                http_client=self._chat_http_client,
                api_key=self._chat_api_key,
                base_url=self._chat_base_url,
                model=self._chat_model_name,
            )
            low_confidence_action = "answer_with_citations"
        elif intent == "general_chat":
            messages = render_general_chat_messages(request, intent)
            answer = build_general_chat_answer()
            low_confidence_action = "general_chat_without_citations"
        else:
            messages = render_quality_checked_rag_messages(request, intent, reliable_hits, low_confidence)
            answer = build_fallback_answer()
            low_confidence_action = "clarify_or_handoff"
        confidence_level = "not_applicable" if intent == "general_chat" else ("low" if low_confidence else "high")
        cost_summary = build_cost_summary(messages, answer)
        top_score = raw_hits[0].score if raw_hits else 0.0
        quality_summary = run_rag_quality_check(embedding_client=self._embedding_client)
        reasoning_summary = [
            "后端先执行 RAG 检索，再根据最高分判断本轮是否低置信。",
            f"本轮最高分为 {top_score}，低置信阈值为 {LOW_CONFIDENCE_THRESHOLD}。",
            "低置信时不返回 citations，不硬编答案；固定问题集用于检查基础召回质量。",
        ]
        session_state = {
            "agent_version": "step-12-rag-quality-fallback",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "rag": {
                "mode": "quality_checked_vector_retrieval",
                "embedding_model": read_embedding_model_name(),
                "top_k": TOP_K,
                "score_threshold": RETRIEVAL_SCORE_THRESHOLD,
                "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
                "raw_retrieved_count": len(raw_hits),
                "retrieved_count": len(reliable_hits),
                "citation_count": len(citations),
                "confidence_level": confidence_level,
                "top_score": top_score,
                "matched_chunk_ids": [hit.chunk.chunk_id for hit in reliable_hits],
                "low_confidence_action": low_confidence_action,
            },
            "cost_observation": {
                "prompt_tokens": cost_summary.prompt_tokens,
                "context_chars": cost_summary.context_chars,
            },
            "rag_quality": {
                # 这是本步固定问题集的轻量检查结果，不是后面完整 Eval 平台。
                "case_count": quality_summary.total_cases,
                "passed_cases": quality_summary.passed_cases,
                "average_recall_at_k": quality_summary.average_recall_at_k,
                "average_precision_at_k": quality_summary.average_precision_at_k,
            },
            "next_gap": "基础 RAG 已经能找依据、给引用和低置信兜底，但命中还不等于命准；下一幕要继续提升召回和排序质量。",
        }

        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent,
            citations=citations,
            cost_summary=cost_summary,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
