from discord_agent_hub.knowledge import KnowledgeChunk, build_knowledge_context, score_chunk, split_text_into_chunks


def test_split_text_into_chunks_uses_overlap():
    chunks = split_text_into_chunks("abcdef", chunk_size=4, overlap=1)

    assert chunks == ["abcd", "def"]


def test_score_chunk_matches_ascii_and_japanese_terms():
    assert score_chunk("payment rails", "Payment rails matter.") > 0
    assert score_chunk("非推奨モデル", "この表では非推奨とされています。") > 0


def test_build_knowledge_context_labels_sources():
    context = build_knowledge_context(
        [
            KnowledgeChunk(
                id="chunk-1",
                source_id="source-a",
                document_id="doc-1",
                chunk_index=2,
                filename="notes.md",
                text="Important fact.",
                score=3,
            )
        ]
    )

    assert "source-a" in context
    assert "notes.md" in context
    assert "Important fact." in context
