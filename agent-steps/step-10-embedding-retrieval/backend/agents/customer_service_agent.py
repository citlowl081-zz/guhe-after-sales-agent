"""Step 10 客服 Agent 编排。

Agent 负责串起粗意图、向量检索、RAG Prompt、模型回答和成本观察。
"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse, Intent, IntentResult
from config.settings import SCORE_THRESHOLD, TOP_K
from cost.observer import build_cost_summary
from embeddings.client import EmbeddingClient, read_embedding_model_name
from models.llm_client import call_chat_model
from rag.prompting import render_vector_rag_messages
from rag.vector_store import retrieve_by_vector


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回用户问题中命中的关键词。"""

    return [keyword for keyword in keywords if keyword.lower() in message]


def classify_intent(user_message: str) -> IntentResult:
    """识别粗意图，用于调试展示和 Prompt 背景说明。"""

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


class Step10Agent:
    """Embedding 与向量检索版 Agent。"""

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient | None = None,
        chat_http_client: httpx.Client | None = None,
        chat_api_key: str | None = None,
        chat_base_url: str | None = None,
        chat_model_name: str | None = None,
    ) -> None:
        """保存 embedding 与聊天模型配置。"""

        self._message_count_by_session: dict[str, int] = {}
        self._embedding_client = embedding_client
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """执行一次向量检索版 RAG 问答。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]
        intent_result = classify_intent(request.user_message)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent_result.intent, matched_keywords=intent_result.matched_keywords)

        # Step 10 的关键变化：先把问题 embedding，再和知识向量库做相似度检索。
        hits = retrieve_by_vector(request.user_message, embedding_client=self._embedding_client)
        log_guhe_event("VECTOR_RETRIEVED", "向量检索完成", observe=True, hit_count=len(hits), threshold=SCORE_THRESHOLD, scores={hit.chunk.chunk_id: hit.score for hit in hits})
        messages = render_vector_rag_messages(request, intent_result, hits)
        answer = call_chat_model(
            messages,
            http_client=self._chat_http_client,
            api_key=self._chat_api_key,
            base_url=self._chat_base_url,
            model=self._chat_model_name,
        )
        cost_summary = build_cost_summary(messages, answer)
        reasoning_summary = [
            "后端调用商用 embedding 模型，把用户问题和知识片段都转成向量。",
            f"本轮按 cosine similarity 返回 {len(hits)} 个高于阈值的 top_k 片段。",
            "Step 10 只讲 embedding 与向量检索，citations 到下一步再进入响应格式。",
        ]
        session_state = {
            "agent_version": "step-10-embedding-retrieval",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "rag": {
                "mode": "vector_retrieval",
                "embedding_model": read_embedding_model_name(),
                "top_k": TOP_K,
                "score_threshold": SCORE_THRESHOLD,
                "retrieved_count": len(hits),
                "matched_chunk_ids": [hit.chunk.chunk_id for hit in hits],
                "scores": {hit.chunk.chunk_id: hit.score for hit in hits},
            },
            "cost_observation": {
                "prompt_tokens": cost_summary.prompt_tokens,
                "context_chars": cost_summary.context_chars,
            },
            "next_gap": "向量检索能找相似片段，但老板还看不到回答来源；下一步要返回 citations。",
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
