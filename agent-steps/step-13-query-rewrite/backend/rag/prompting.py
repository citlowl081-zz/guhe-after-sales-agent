"""Step 13 RAG Prompt 和 citation 构造。"""

from __future__ import annotations

from api.schemas import ChatRequest, Citation, KnowledgeHit, QueryRewrite


def build_citations(hits: list[KnowledgeHit]) -> list[Citation]:
    """把可靠命中转换成 citations。"""

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


def render_rag_messages(request: ChatRequest, rewrite: QueryRewrite, hits: list[KnowledgeHit]) -> list[dict[str, str]]:
    """把改写查询和命中知识渲染成模型输入。"""

    evidence = "\n".join(
        f"[{index}] {hit.chunk.title} ({hit.chunk.status})\n{hit.chunk.text}"
        for index, hit in enumerate(hits, start=1)
    )
    return [
        {
            "role": "system",
            "content": (
                "你是古河商城公司的客服 Agent。只能依据给定知识回答稳定规则问题；"
                "涉及实时价格、库存、订单、物流或退款进度时，要说明需要业务系统核验。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户原话：{request.user_message}\n"
                f"检索改写：{rewrite.rewritten_query}\n"
                f"命中知识：\n{evidence}"
            ),
        },
    ]
