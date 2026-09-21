"""Step 11 citations Agent。

本步沿用 Step 10 向量检索，但把命中结果转成 citations，避免让模型自己编出处。
"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_chat

import httpx

from api.schemas import ChatRequest, ChatResponse, Citation, Intent, KnowledgeHit
from config.settings import SCORE_THRESHOLD, TOP_K
from cost.observer import build_cost_summary
from embeddings.client import EmbeddingClient, read_embedding_model_name
from models.llm_client import call_chat_model
from rag.vector_store import retrieve_by_vector


def build_citations(hits: list[KnowledgeHit]) -> list[Citation]:
    """把真实命中的知识片段转换成响应里的 citations。"""

    # citations 必须来自真实命中片段，不能让模型自己编“出处”。
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
    """没有可靠命中时生成固定兜底回答。"""

    # 没有依据时直接承认，不为了回答好看而硬编规则。
    return "我暂时没有在当前知识库里找到可靠依据，不能直接给出规则结论。"


def build_general_chat_answer() -> str:
    """普通客服寒暄不需要 RAG 依据，也不应该被知识命中卡住。"""

    return (
        "你好，我是古河商城公司的客服 Agent。你可以直接描述商品、活动、发货、物流、发票或售后问题；"
        "涉及具体规则时，我会优先依据当前知识库回答，找不到可靠依据时不会编造。"
    )


def render_cited_rag_messages(request: ChatRequest, intent: Intent, hits: list[KnowledgeHit]) -> list[dict[str, str]]:
    """把带 citations 的检索命中渲染成模型输入。"""
    context = "\n\n".join(f"[{hit.chunk.chunk_id} | score={hit.score}]\n{hit.chunk.text}" for hit in hits)
    if not context:
        context = "当前问题没有达到向量检索阈值。回答时要承认没有可靠依据。"
    return [
        {
            "role": "system",
            "content": (
                "你是古河商城公司的客服 Agent。当前回答只能依据本轮向量检索命中的知识片段。"
                "citations 由后端根据真实命中生成，不能由模型编造。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户原话：{request.user_message}\n"
                f"粗意图：{intent}\n"
                f"当前用户：{request.runtime_user_id}，会员等级：{request.runtime_member_level or '未提供'}\n"
                f"命中知识：\n{context}"
            ),
        },
    ]


def render_general_chat_messages(request: ChatRequest, intent: Intent) -> list[dict[str, str]]:
    """记录普通客服回答的输入边界，便于成本观察和运行调试。"""

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
    """沿用已有意图字段，不因为新增 citations 就改掉旧接口约定。"""

    message = user_message.strip().lower()
    if any(word in message for word in ["投诉", "举报", "赔偿", "曝光", "315"]):
        return "complaint"
    if any(word in message for word in ["退款", "退货", "取消订单", "坏了", "无法开机", "质量问题"]):
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
        # 如果知识命中了但文本意图不明显，先落到产品/知识咨询，等真实路由需求出现后再细分。
        return "product_consult"
    return "unknown"


class Step11Agent:
    """返回 citations 的 RAG Agent。"""

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

        # 仍然保留最小会话状态，便于调试后台观察同一会话的请求次数。
        self._message_count_by_session: dict[str, int] = {}
        self._embedding_client = embedding_client
        self._chat_http_client = chat_http_client
        self._chat_api_key = chat_api_key
        self._chat_base_url = chat_base_url
        self._chat_model_name = chat_model_name

    @observe_chat
    def chat(self, request: ChatRequest) -> ChatResponse:
        """执行一次向量检索，并把命中知识转成 citations。"""

        self._message_count_by_session[request.session_id] = (
            self._message_count_by_session.get(request.session_id, 0) + 1
        )
        message_count = self._message_count_by_session[request.session_id]

        # 这一版核心链路：沿用 Step 10 向量命中 -> citations -> 带依据回答。
        hits = retrieve_by_vector(request.user_message, embedding_client=self._embedding_client)
        log_guhe_event("VECTOR_RETRIEVED", "向量检索完成", observe=True, hit_count=len(hits), scores={hit.chunk.chunk_id: hit.score for hit in hits})
        citations = build_citations(hits)
        log_guhe_event("CITATIONS_BUILT", "引用证据生成完成", observe=True, citation_count=len(citations), citation_ids=[item.citation_id for item in citations])
        intent = classify_intent(request.user_message, hits)
        log_guhe_event("INTENT_CLASSIFIED", "意图识别完成", intent=intent)
        if hits:
            messages = render_cited_rag_messages(request, intent, hits)
            answer = call_chat_model(
                messages,
                http_client=self._chat_http_client,
                api_key=self._chat_api_key,
                base_url=self._chat_base_url,
                model=self._chat_model_name,
            )
            answer_path = "answer_with_citations"
        elif intent == "general_chat":
            messages = render_general_chat_messages(request, intent)
            answer = build_general_chat_answer()
            answer_path = "general_chat_without_citations"
        else:
            messages = render_cited_rag_messages(request, intent, hits)
            answer = build_fallback_answer()
            answer_path = "no_reliable_knowledge_fallback"
        cost_summary = build_cost_summary(messages, answer)
        reasoning_summary = [
            "后端接收 ChatRequest，并对用户问题执行知识检索。",
            f"本次返回 {len(hits)} 个知识命中，并转成 citations。",
            "普通寒暄可以不依赖 citations；业务规则结论仍然必须有可靠知识依据。",
        ]
        session_state = {
            "agent_version": "step-11-rag-citations",
            "message_count": message_count,
            "runtime_context": {
                "user_id": request.runtime_user_id,
                "nickname": request.runtime_nickname,
                "member_level": request.runtime_member_level,
                "risk_level": request.runtime_risk_level,
                "page_context": request.runtime_context or {},
            },
            "rag": {
                # RAG 运行状态要进 session_state，排查“为什么引用错了”才有入口。
                "mode": "vector_retrieval",
                "query": request.user_message,
                "embedding_model": read_embedding_model_name(),
                "top_k": TOP_K,
                "score_threshold": SCORE_THRESHOLD,
                "retrieved_count": len(hits),
                "citation_count": len(citations),
                "answer_path": answer_path,
                "matched_chunk_ids": [hit.chunk.chunk_id for hit in hits],
                "scores": {hit.chunk.chunk_id: hit.score for hit in hits},
            },
            "cost_observation": {
                "prompt_tokens": cost_summary.prompt_tokens,
                "context_chars": cost_summary.context_chars,
            },
            "next_gap": "有引用不代表引用一定命对，接下来还要处理召回质量和低置信兜底。",
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
