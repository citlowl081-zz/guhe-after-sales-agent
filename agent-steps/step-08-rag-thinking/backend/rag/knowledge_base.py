"""Step 08 基础 RAG 知识库。

这一步还不用向量库，先用 Markdown 元数据、关键词和粗意图证明：
回答前应该先选择相关知识，而不是把所有规则塞进 Prompt。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from api.schemas import ChatRequest, Intent, IntentResult, KnowledgeHit, KnowledgeSection, KnowledgeSnippet, SourceDocument
from config.settings import KNOWLEDGE_DIR, RAG_TOP_K


def parse_metadata_value(value: str) -> Any:
    """解析 Markdown front matter 或章节注释里的简单元数据值。"""

    parsed_value = value.strip()
    if parsed_value.startswith("[") and parsed_value.endswith("]"):
        return [
            item.strip().strip("\"'")
            for item in parsed_value[1:-1].split(",")
            if item.strip()
        ]
    if "," in parsed_value:
        return [
            item.strip().strip("\"'")
            for item in parsed_value.split(",")
            if item.strip()
        ]
    return parsed_value


def parse_front_matter(markdown: str) -> tuple[dict[str, Any], str]:
    """拆出 Markdown 文档顶部的元数据和正文。"""

    if not markdown.startswith("---"):
        return {}, markdown

    end_marker = markdown.find("\n---", 3)
    if end_marker == -1:
        return {}, markdown

    raw_metadata = markdown[3:end_marker].strip()
    body = markdown[end_marker + 4 :].lstrip()
    metadata: dict[str, Any] = {}
    for raw_line in raw_metadata.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = parse_metadata_value(value)
    return metadata, body


def parse_section_metadata(line: str) -> dict[str, Any]:
    """解析章节前的 HTML 注释元数据。"""

    stripped = line.strip()
    if not stripped.startswith("<!--") or not stripped.endswith("-->"):
        return {}

    raw_metadata = stripped.removeprefix("<!--").removesuffix("-->").strip()
    metadata: dict[str, Any] = {}
    for part in raw_metadata.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        metadata[key.strip()] = parse_metadata_value(value)
    return metadata


def load_source_documents() -> list[SourceDocument]:
    """读取 knowledge 目录里的所有 Markdown 原文。"""

    documents: list[SourceDocument] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        metadata, body = parse_front_matter(path.read_text(encoding="utf-8"))
        title = str(metadata.get("title") or path.stem)
        documents.append(SourceDocument(source_path=path.name, title=title, metadata=metadata, body=body))
    return documents


def parse_sections(document: SourceDocument) -> list[KnowledgeSection]:
    """把一篇 Markdown 粗切成业务章节。"""

    sections: list[KnowledgeSection] = []
    current_title = document.title
    current_lines: list[str] = []
    current_section_metadata: dict[str, Any] = {}
    section_index = 0

    def flush_section() -> None:
        """把当前累计的章节内容写入 sections。"""

        nonlocal section_index, current_lines, current_title, current_section_metadata
        text = "\n".join(line for line in current_lines if line.strip()).strip()
        if not text:
            current_lines = []
            current_section_metadata = {}
            return

        section_index += 1
        raw_keywords = current_section_metadata.get("keywords") or document.metadata.get("tags") or []
        keywords = [str(keyword).strip() for keyword in raw_keywords if str(keyword).strip()]
        snippet_id = current_section_metadata.get("chunk_id")
        sections.append(
            KnowledgeSection(
                source_path=document.source_path,
                document_title=document.title,
                section_index=section_index,
                section=current_title,
                snippet_id=str(snippet_id) if snippet_id else None,
                keywords=keywords,
                effective_status=str(
                    current_section_metadata.get("effective_status")
                    or document.metadata.get("effective_status")
                    or "active"
                ),
                text=text,
            )
        )
        current_lines = []
        current_section_metadata = {}

    for raw_line in document.body.splitlines():
        line = raw_line.rstrip()
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            flush_section()
            current_title = line.removeprefix("## ").strip()
            continue
        parsed_metadata = parse_section_metadata(line)
        if parsed_metadata:
            current_section_metadata.update(parsed_metadata)
            continue
        current_lines.append(line)

    flush_section()
    return sections


def topic_from_document(document: SourceDocument) -> Intent:
    """根据文档 domain 粗略映射到客服意图。"""

    domain = str(document.metadata.get("domain") or "")
    return {
        "promotion": "promotion_consult",
        "after_sale": "refund_request",
        "shipping": "order_query",
        "product": "product_consult",
    }.get(domain, "unknown")


def load_knowledge_snippets() -> list[KnowledgeSnippet]:
    """把所有 Markdown 文档转成 Step 08 使用的知识片段。"""

    snippets: list[KnowledgeSnippet] = []
    for document in load_source_documents():
        topic = topic_from_document(document)
        for section in parse_sections(document):
            snippet_id = section.snippet_id or f"{Path(section.source_path).stem}-s{section.section_index}"
            snippets.append(
                KnowledgeSnippet(
                    snippet_id=snippet_id,
                    title=section.section,
                    topic=topic,
                    keywords=section.keywords,
                    effective_status=section.effective_status,
                    text=section.text,
                )
            )
    return snippets


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回一段文本命中的知识关键词。"""

    return [keyword for keyword in keywords if keyword.lower() in message]


def retrieve_relevant_knowledge(user_message: str, intent: Intent) -> list[KnowledgeHit]:
    """按关键词和粗意图找出本轮最相关的知识片段。"""

    message = user_message.lower()
    hits: list[KnowledgeHit] = []
    for snippet in load_knowledge_snippets():
        asks_for_history = any(word in message for word in ["历史", "复盘", "双11", "2024"])
        if snippet.effective_status != "active" and not asks_for_history:
            continue

        matched = first_matched_keywords(message, snippet.keywords)
        if not matched:
            continue

        # Step 08 先用最小可理解的相关性：关键词命中 + 当前粗意图加权。
        keyword_score = len(matched) / max(len(snippet.keywords), 1)
        topic_boost = 0.2 if snippet.topic == intent else 0
        score = min(1.0, keyword_score + topic_boost)
        hits.append(KnowledgeHit(snippet=snippet, score=round(score, 3), matched_keywords=matched))

    return sorted(hits, key=lambda hit: hit.score, reverse=True)[:RAG_TOP_K]


def render_rag_messages(
    request: ChatRequest,
    intent_result: IntentResult,
    hits: list[KnowledgeHit],
) -> list[dict[str, str]]:
    """把检索命中的知识渲染成模型可读的 messages。"""

    retrieved_context = "\n\n".join(
        f"[{hit.snippet.snippet_id} | score={hit.score}]\n{hit.snippet.text}" for hit in hits
    )
    if not retrieved_context:
        retrieved_context = "当前问题没有命中相关知识。回答时要说明没有可靠依据，不能编规则。"

    system_message = (
        "你是古河商城公司的客服 Agent。当前版本开始使用基础 RAG 思路："
        "只把本轮检索到的相关知识交给模型，不再把所有规则全文塞进 Prompt。"
        "如果检索结果不足，必须承认没有可靠依据。"
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
        "本轮检索到的相关知识：\n"
        f"{retrieved_context}\n"
        "\n"
        "用户原话：\n"
        f"{request.user_message}"
    )
    return [{"role": "system", "content": system_message}, {"role": "user", "content": user_message}]
