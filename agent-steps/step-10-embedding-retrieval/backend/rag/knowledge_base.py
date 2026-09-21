"""Step 10 Markdown 到知识 chunk 的处理。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from api.schemas import KnowledgeChunk, KnowledgeSection, SourceDocument
from config.settings import CHUNK_OVERLAP, CHUNK_SIZE, KNOWLEDGE_DIR


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
    """把 Markdown 文档按二级标题解析成业务章节。"""

    sections: list[KnowledgeSection] = []
    current_title = document.title
    current_lines: list[str] = []
    current_section_metadata: dict[str, Any] = {}
    section_index = 0

    def flush_section() -> None:
        """把当前累计章节写入 sections。"""

        nonlocal section_index, current_lines, current_title, current_section_metadata
        text = "\n".join(line for line in current_lines if line.strip()).strip()
        if not text:
            current_lines = []
            current_section_metadata = {}
            return

        section_index += 1
        raw_keywords = current_section_metadata.get("keywords") or document.metadata.get("tags") or []
        keywords = [str(keyword).strip() for keyword in raw_keywords if str(keyword).strip()]
        chunk_id = current_section_metadata.get("chunk_id")
        effective_status = str(
            current_section_metadata.get("effective_status")
            or document.metadata.get("effective_status")
            or "active"
        )
        sections.append(
            KnowledgeSection(
                source_path=document.source_path,
                document_title=document.title,
                section_index=section_index,
                section=current_title,
                chunk_id=str(chunk_id) if chunk_id else None,
                keywords=keywords,
                effective_status=effective_status,
                text=text,
                metadata={
                    **document.metadata,
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
    """从默认 knowledge 目录构建可向量化的知识 chunk。"""

    chunks: list[KnowledgeChunk] = []
    for document in load_source_documents():
        for section in parse_sections(document):
            section_chunks = split_into_chunks(section.text, chunk_size, overlap)
            for chunk_index, chunk_text in enumerate(section_chunks, start=1):
                base_chunk_id = section.chunk_id or f"{Path(section.source_path).stem}-s{section.section_index}"
                chunk_id = base_chunk_id if len(section_chunks) == 1 else f"{base_chunk_id}-c{chunk_index}"
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=chunk_id,
                        title=section.section,
                        source_path=section.source_path,
                        section=section.section,
                        keywords=section.keywords,
                        effective_status=section.effective_status,
                        text=chunk_text,
                    )
                )
    return chunks


def load_knowledge_chunks() -> list[KnowledgeChunk]:
    """加载当前步默认知识 chunk。"""

    return build_knowledge_chunks()


def query_asks_for_history(query: str) -> bool:
    """判断用户是否明确询问历史活动或复盘内容。"""

    lowered_query = query.lower()
    return any(term in lowered_query for term in ["历史", "复盘", "双11", "2024"])


def should_include_chunk_for_query(chunk: KnowledgeChunk, asks_for_history: bool) -> bool:
    """根据有效状态和问题意图决定 chunk 是否进入候选集。"""

    if chunk.effective_status == "active":
        return True
    # 用户明确问历史活动或复盘时，允许 historical 片段参与检索；普通咨询默认只看当前有效规则。
    return asks_for_history
