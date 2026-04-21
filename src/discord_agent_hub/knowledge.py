from __future__ import annotations

import re
from dataclasses import dataclass


CHUNK_SIZE = 3000
CHUNK_OVERLAP = 300


@dataclass(slots=True)
class KnowledgeChunk:
    id: str
    source_id: str
    document_id: str
    chunk_index: int
    filename: str
    text: str
    score: int = 0


def split_text_into_chunks(text: str, *, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_size)
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]


def score_chunk(query: str, text: str) -> int:
    query_terms = _terms(query)
    if not query_terms:
        return 0
    haystack = text.lower()
    score = 0
    for term in query_terms:
        if term in haystack:
            score += 3 if len(term) > 1 else 1
    return score


def build_knowledge_context(chunks: list[KnowledgeChunk]) -> str:
    if not chunks:
        return ""
    segments = [
        "The following excerpts were retrieved from the agent knowledge base. "
        "Use them as grounding context when relevant, and do not assume they are exhaustive."
    ]
    for chunk in chunks:
        segments.append(
            "\n".join(
                [
                    f"[Knowledge source: {chunk.source_id}; file: {chunk.filename}; chunk: {chunk.chunk_index}]",
                    chunk.text.strip(),
                ]
            )
        )
    return "\n\n".join(segments).strip()


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    ascii_terms = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    cjk_runs = re.findall(r"[\u3040-\u30ff\u3400-\u9fff]{2,}", lowered)
    cjk_terms: set[str] = set()
    for run in cjk_runs:
        cjk_terms.add(run)
        cjk_terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return ascii_terms | cjk_terms
