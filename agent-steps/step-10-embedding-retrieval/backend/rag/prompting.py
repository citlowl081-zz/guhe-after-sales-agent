"""向量检索版 RAG Prompt 渲染。"""

from __future__ import annotations

from api.schemas import ChatRequest, IntentResult, KnowledgeHit


def render_vector_rag_messages(
    request: ChatRequest,
    intent_result: IntentResult,
    hits: list[KnowledgeHit],
) -> list[dict[str, str]]:
    """把向量命中的知识片段渲染成模型可读 messages。"""

    context = "\n\n".join(
        f"[{hit.chunk.chunk_id} | score={hit.score}]\n{hit.chunk.text}" for hit in hits
    )
    if not context:
        context = "当前问题没有达到向量检索阈值。回答时要承认没有可靠依据。"

    system_message = (
        "你是古河商城公司的客服 Agent。当前版本用 embedding 把问题和知识片段变成向量，"
        "再用相似度选出 top_k 片段。回答只能依据本轮命中的片段。"
    )
    user_message = (
        "古河商城系统确认的当前用户事实：\n"
        f"- user_id: {request.runtime_user_id}\n"
        f"- member_level: {request.runtime_member_level or '未提供'}\n"
        f"- risk_level: {request.runtime_risk_level or '未提供'}\n"
        "\n"
        f"粗意图：{intent_result.intent}\n"
        f"粗意图说明：{intent_result.explanation}\n"
        "\n"
        "本轮向量检索命中的知识片段：\n"
        f"{context}\n"
        "\n"
        "用户原话：\n"
        f"{request.user_message}"
    )
    return [{"role": "system", "content": system_message}, {"role": "user", "content": user_message}]
