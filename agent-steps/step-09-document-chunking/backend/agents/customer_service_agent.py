"""Step 09 客服 Agent 编排。

Agent 启动时加载并切好知识，聊天时复用这些 chunk，避免每次请求都重新读文档。
"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse, Intent, IntentResult
from config.settings import CHUNK_OVERLAP, CHUNK_SIZE, RAG_TOP_K
from cost.observer import build_cost_summary
from models.llm_client import call_chat_model
from rag.knowledge_base import (
    build_knowledge_chunks_from_documents,
    load_source_documents,
    render_chunked_rag_messages,
    retrieve_chunks,
)


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回用户问题里命中的关键词。"""

    return [keyword for keyword in keywords if keyword.lower() in message]


def classify_intent(user_message: str) -> IntentResult:
    """用关键词识别粗意图，为后续检索提供调试线索。"""

    message = user_message.strip().lower()
    intent_rules: list[tuple[Intent, list[str], str]] = [
        ("complaint", ["投诉", "举报", "赔偿", "曝光", "315", "别踢皮球"], "用户表达了投诉或强烈不满。"),
        ("refund_request", ["退款", "退货", "取消订单", "坏了", "无法开机", "质量问题", "无理由"], "用户在询问退款、退货或质量问题。"),
        ("order_query", ["订单", "物流", "快递", "发货", "到哪", "运单"], "用户在询问订单或物流状态。"),
        ("promotion_consult", ["优惠", "活动", "会员价", "券", "满减", "折扣"], "用户在询问优惠或活动。"),
        ("product_consult", ["耳机", "充电器", "音箱", "推荐", "哪个好"], "用户在询问商品或推荐。"),
        ("general_chat", ["你好", "您好", "在吗", "谢谢"], "用户只是普通问候。"),
    ]
    for intent, keywords, explanation in intent_rules:
        matched = first_matched_keywords(message, keywords)
        if matched:
            return IntentResult(intent=intent, matched_keywords=matched, explanation=explanation)
    return IntentResult(intent="unknown", matched_keywords=[], explanation="没有命中当前版本的粗意图规则。")


class Step09Agent:
    """文档切片与 metadata 版 Agent。"""

    def __init__(
        self,
        *,
        chat_http_client: httpx.Client | None = None,
        chat_api_key: str | None = None,
        chat_base_url: str | None = None,
        chat_model_name: str | None = None,
    ) -> None:
        """初始化模型配置，并在启动时构建知识 chunk。"""

        self._message_count_by_session: dict[str, int] = {}
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

        # 生产中的知识索引通常不是每个请求临时重建；本步先用启动时缓存表达这个边界。
        self._source_documents = load_source_documents()
        self._knowledge_chunks = build_knowledge_chunks_from_documents(self._source_documents)

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """执行一次基于 chunk 的 RAG 问答。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent_result = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent_result.intent, matched_keywords=intent_result.matched_keywords)
        chunks = self._knowledge_chunks
        log_guhe_event("KNOWLEDGE_CHUNKS_READY", "知识切片已就绪", observe=True, document_count=len(self._source_documents), chunk_count=len(chunks), chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        hits = retrieve_chunks(request.user_message, chunks)
        log_guhe_event("RAG_RETRIEVED", "知识切片检索完成", observe=True, hit_count=len(hits), chunk_ids=[hit.chunk.chunk_id for hit in hits])
        messages = render_chunked_rag_messages(request, intent_result, hits)
        answer = call_chat_model(
            messages,
            http_client=self._chat_http_client,
            api_key=self._chat_api_key,
            base_url=self._chat_base_url,
            model=self._chat_model_name,
        )
        cost_summary = build_cost_summary(messages, answer)
        reasoning_summary = [
            "后端启动时读取 Markdown 知识文档，并按章节切成知识片段。",
            f"本轮从 {len(chunks)} 个 chunk 中命中 {len(hits)} 个相关片段。",
            "Step 09 只整理可检索知识结构，还没有接入向量检索或 citations。",
        ]
        session_state = {
            "agent_version": "step-09-document-chunking",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "rag": {
                "mode": "markdown_chunking",
                "document_count": len(self._source_documents),
                "chunk_count": len(chunks),
                "chunk_size": CHUNK_SIZE,
                "chunk_overlap": CHUNK_OVERLAP,
                "top_k": RAG_TOP_K,
                "retrieved_count": len(hits),
                "matched_chunk_ids": [hit.chunk.chunk_id for hit in hits],
                "matched_sections": [hit.chunk.section for hit in hits],
            },
            "cost_observation": {
                "prompt_tokens": cost_summary.prompt_tokens,
                "context_chars": cost_summary.context_chars,
            },
            "next_gap": "知识已经切成片段，但现在仍靠关键词命中；下一步要把文本变成向量再检索。",
        }
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            intent=intent_result.intent,
            intent_result=intent_result,
            cost_summary=cost_summary,
            reasoning_summary=reasoning_summary,
            session_state=session_state,
        )
