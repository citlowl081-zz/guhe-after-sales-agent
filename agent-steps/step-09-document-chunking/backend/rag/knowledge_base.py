"""Step 09 文档切片与 metadata 管道。

这一步的重点不是更聪明的排序，而是让知识先变成可治理的结构：
文档级 metadata、章节级 metadata、chunk 参数和稳定 chunk_id。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from api.schemas import ChatRequest, IntentResult, KnowledgeChunk, KnowledgeHit, KnowledgeSection, SourceDocument
from config.settings import CHUNK_OVERLAP, CHUNK_SIZE, KNOWLEDGE_DIR, RAG_TOP_K


def parse_metadata_value(value: str) -> Any:
    """解析 Markdown 元数据里的简单值。"""

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
    """拆出 Markdown 顶部 front matter 和正文。"""

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
    """解析章节前 HTML 注释形式的 metadata。"""

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
    if "section_id" not in metadata and "chunk_id" in metadata:
        metadata["section_id"] = metadata["chunk_id"]
    return metadata


def extract_markdown_title(markdown: str) -> str | None:
    """从 Markdown 正文里提取一级标题。"""

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return None


def infer_document_metadata(source_path: str, title: str) -> dict[str, Any]:
    """根据知识文件名补齐文档级 metadata。"""

    if source_path == "after_sale_policy.md":
        return {
            "title": title,
            "domain": "after_sale",
            "source_type": "policy",
            "effective_status": "active",
            "risk_level": "high",
            "owner": "after_sale_team",
            "tags": ["售后", "退货", "退款", "换货", "规则"],
        }
    if source_path == "shipping_faq.md":
        return {
            "title": title,
            "domain": "shipping",
            "source_type": "faq",
            "effective_status": "active",
            "risk_level": "medium",
            "owner": "logistics_service_team",
            "tags": ["物流", "发票", "优惠", "FAQ"],
        }
    if source_path == "product_guide.md":
        return {
            "title": title,
            "domain": "product",
            "source_type": "product_guide",
            "effective_status": "active",
            "risk_level": "low",
            "owner": "product_team",
            "tags": ["商品", "推荐", "参数", "导购"],
        }
    return {"title": title, "domain": "general", "source_type": "knowledge", "effective_status": "active", "tags": []}


def infer_section_metadata(source_path: str, section_title: str) -> dict[str, Any]:
    """根据章节标题补齐章节级业务 metadata。"""

    if source_path == "after_sale_policy.md":
        metadata: dict[str, Any] = {"domain": "after_sale", "risk_level": "high"}
        if "未发货" in section_title:
            metadata.update({"section_id": "refund-before-shipping", "scene_key": "refund_before_shipping", "keywords": ["未发货", "退款", "取消订单", "待发货"]})
        elif "签收" in section_title or "7 天" in section_title:
            metadata.update({"section_id": "return-after-delivery", "scene_key": "return_after_delivery", "keywords": ["签收", "7天", "七天", "退货", "无理由", "配件"]})
        elif "质量" in section_title or "换货" in section_title:
            metadata.update({"section_id": "quality-issue-exchange", "scene_key": "quality_issue_exchange", "keywords": ["质量问题", "换货", "无法开机", "凭证"]})
        elif "拒收" in section_title:
            metadata.update({"section_id": "reject-after-shipping", "scene_key": "reject_after_shipping", "keywords": ["已发货", "拒收", "退款", "物流"]})
        else:
            metadata.update({"section_id": "after-sale-boundary", "scene_key": "after_sale_boundary", "keywords": ["售后", "退款", "退货", "人工"]})
        return metadata
    if source_path == "shipping_faq.md":
        metadata = {"domain": "shipping", "risk_level": "medium"}
        if "发票" in section_title:
            metadata.update({"domain": "invoice", "section_id": "invoice-issue", "scene_key": "invoice_issue", "keywords": ["发票", "抬头", "税号", "红冲"]})
        elif "会员" in section_title or "优惠" in section_title:
            metadata.update({"domain": "promotion", "section_id": "promotion-faq", "scene_key": "promotion_faq", "keywords": ["会员", "优惠", "优惠券", "结算页"]})
        elif "物流" in section_title or "配送" in section_title:
            metadata.update({"section_id": "shipping-tracking-boundary", "scene_key": "shipping_tracking_boundary", "keywords": ["物流", "配送", "运单", "延迟"]})
        else:
            metadata.update({"section_id": "shipping-faq", "scene_key": "shipping_faq", "keywords": ["发货", "物流", "FAQ"]})
        return metadata
    if source_path == "product_guide.md":
        metadata = {"domain": "product", "risk_level": "low"}
        if "耳机" in section_title:
            metadata.update({"section_id": "commute-headphone", "scene_key": "commute_headphone", "keywords": ["耳机", "降噪", "通勤", "推荐"]})
        elif "充电器" in section_title:
            metadata.update({"section_id": "gan-charger", "scene_key": "gan_charger", "keywords": ["充电器", "快充", "65W", "推荐"]})
        elif "音箱" in section_title:
            metadata.update({"section_id": "outdoor-speaker", "scene_key": "outdoor_speaker", "keywords": ["音箱", "户外", "露营", "推荐"]})
        else:
            metadata.update({"section_id": "product-recommendation", "scene_key": "product_recommendation", "keywords": ["商品", "推荐", "库存", "价格"]})
        return metadata
    return {"section_id": section_title, "scene_key": section_title, "keywords": []}


def load_source_documents() -> list[SourceDocument]:
    """读取 knowledge 目录，并为每篇文档补齐基础 metadata。"""

    documents: list[SourceDocument] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        metadata, body = parse_front_matter(path.read_text(encoding="utf-8"))
        title = str(metadata.get("title") or extract_markdown_title(body) or path.stem)
        metadata = {**infer_document_metadata(path.name, title), **metadata}
        documents.append(SourceDocument(source_path=path.name, title=title, metadata=metadata, body=body))
    return documents


def parse_sections(document: SourceDocument) -> list[KnowledgeSection]:
    """按 Markdown 二级标题拆章节，并合并文档级与章节级 metadata。"""

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
        inferred_metadata = infer_section_metadata(document.source_path, current_title)
        sections.append(
            KnowledgeSection(
                source_path=document.source_path,
                document_title=document.title,
                section_index=section_index,
                section=current_title,
                text=text,
                metadata={
                    **document.metadata,
                    **inferred_metadata,
                    **current_section_metadata,
                    "source_path": document.source_path,
                    "document_title": document.title,
                    "section": current_title,
                    "section_index": section_index,
                },
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


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """按长度和 overlap 把章节文本切成 chunk。"""

    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= chunk_size:
        return [normalized]

    step = max(1, chunk_size - overlap)
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunks.append(normalized[start:end])
        if end == len(normalized):
            break
        start += step
    return chunks


def build_knowledge_chunks(
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[KnowledgeChunk]:
    """从默认 knowledge 目录构建全部 chunk。"""

    return build_knowledge_chunks_from_documents(load_source_documents(), chunk_size, overlap)


def build_knowledge_chunks_from_documents(
    documents: list[SourceDocument],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[KnowledgeChunk]:
    """把已读取的文档列表转换成可检索的 KnowledgeChunk。"""

    chunks: list[KnowledgeChunk] = []
    for document in documents:
        for section in parse_sections(document):
            for chunk_index, chunk_text in enumerate(split_into_chunks(section.text, chunk_size, overlap), start=1):
                base_chunk_id = section.metadata.get("section_id") or f"{Path(section.source_path).stem}-s{section.section_index}"
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"{base_chunk_id}-c{chunk_index}",
                        source_path=section.source_path,
                        document_title=section.document_title,
                        section=section.section,
                        chunk_index=chunk_index,
                        text=chunk_text,
                        metadata={
                            **section.metadata,
                            "chunk_size": chunk_size,
                            "chunk_overlap": overlap,
                            "chunk_index": chunk_index,
                        },
                    )
                )
    return chunks


QUERY_TERMS = [
    "金卡",
    "会员",
    "会员价",
    "优惠",
    "优惠券",
    "叠加",
    "结算页",
    "降噪",
    "耳机",
    "退货",
    "退款",
    "签收",
    "7天",
    "七天",
    "无理由",
    "包装",
    "配件",
    "发货",
    "物流",
    "预售",
    "48小时",
]


def first_matched_keywords(message: str, keywords: list[str]) -> list[str]:
    """返回文本中命中的关键词或检索词。"""

    return [keyword for keyword in keywords if keyword.lower() in message]


def retrieve_chunks(user_message: str, chunks: list[KnowledgeChunk]) -> list[KnowledgeHit]:
    """从已构建 chunk 中按关键词命中召回最相关片段。"""

    query_text = user_message.lower()
    query_terms = first_matched_keywords(query_text, QUERY_TERMS)
    asks_for_history = any(term in query_text for term in ["历史", "复盘", "双11", "2024"])
    hits: list[KnowledgeHit] = []
    for chunk in chunks:
        if chunk.metadata.get("effective_status") != "active" and not asks_for_history:
            continue
        searchable_text = " ".join(
            [
                chunk.document_title,
                chunk.section,
                chunk.text,
                " ".join(str(item) for item in chunk.metadata.get("tags", [])),
                " ".join(str(item) for item in chunk.metadata.get("keywords", [])),
                str(chunk.metadata.get("domain", "")),
            ]
        ).lower()
        matched = [term for term in query_terms if term.lower() in searchable_text]
        if not matched:
            continue
        score = min(1.0, len(matched) / max(len(query_terms), 1))
        hits.append(KnowledgeHit(chunk=chunk, score=round(score, 3), matched_terms=matched))
    return sorted(hits, key=lambda hit: hit.score, reverse=True)[:RAG_TOP_K]


def render_chunked_rag_messages(
    request: ChatRequest,
    intent_result: IntentResult,
    hits: list[KnowledgeHit],
) -> list[dict[str, str]]:
    """把命中的 chunk 渲染成模型可读的 RAG Prompt。"""

    context = "\n\n".join(
        (
            f"[{hit.chunk.chunk_id} | {hit.chunk.source_path} | {hit.chunk.section} | score={hit.score}]\n"
            f"{hit.chunk.text}"
        )
        for hit in hits
    )
    if not context:
        context = "当前问题没有命中切片后的知识。回答时要承认没有可靠依据。"

    system_message = (
        "你是古河商城公司的客服 Agent。当前版本把 Markdown 知识按章节切成片段，"
        "并携带 metadata 后再进入 RAG 上下文。回答只能依据本轮命中的片段。"
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
        "本轮命中的知识片段：\n"
        f"{context}\n"
        "\n"
        "用户原话：\n"
        f"{request.user_message}"
    )
    return [{"role": "system", "content": system_message}, {"role": "user", "content": user_message}]
