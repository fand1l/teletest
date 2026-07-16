import pytest
from unittest.mock import patch, MagicMock


class FakeEncoder:
    """Deterministic fake tokenizer: 1 word = 1 token."""
    def encode(self, text):
        return text.split()

    def decode(self, tokens):
        return ' '.join(tokens)


class FakeBudgetManager:
    encoder = FakeEncoder()

    def estimate_tokens(self, text):
        return len(text.split())


@pytest.fixture
def chunker():
    fake = FakeBudgetManager()
    with patch("src.core.rag.chunker.budget_manager", fake):
        from src.core.rag import chunker as chunker_module
        yield chunker_module


def test_short_text_single_chunk(chunker):
    chunks = chunker.chunk_text("Коротке повідомлення.", max_tokens=100, overlap_tokens=10)
    assert chunks == ["Коротке повідомлення."]


def test_empty_text(chunker):
    assert chunker.chunk_text("", max_tokens=100, overlap_tokens=10) == []
    assert chunker.chunk_text("   ", max_tokens=100, overlap_tokens=10) == []


def test_split_respects_max_tokens(chunker):
    # 10 sentences x 5 words each = 50 tokens; max 20 per chunk
    text = " ".join(f"Речення номер {i} має слова." for i in range(10))
    chunks = chunker.chunk_text(text, max_tokens=20, overlap_tokens=5)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.split()) <= 20 + 5  # chunk + seeded overlap headroom


def test_overlap_present_between_chunks(chunker):
    text = " ".join(f"Речення номер {i} має слова." for i in range(10))
    chunks = chunker.chunk_text(text, max_tokens=20, overlap_tokens=6)
    # The tail of chunk N must reappear at the head of chunk N+1
    for prev, nxt in zip(chunks, chunks[1:]):
        last_sentence = prev.split('.')[-2].strip()  # last full sentence
        assert last_sentence in nxt


def test_oversized_sentence_hard_split(chunker):
    # One "sentence" with 50 words and no punctuation, max 10 tokens
    text = " ".join(f"слово{i}" for i in range(50))
    chunks = chunker.chunk_text(text, max_tokens=10, overlap_tokens=0)
    assert len(chunks) >= 5
    joined = " ".join(chunks)
    for i in range(50):
        assert f"слово{i}" in joined


def test_split_sentences_paragraphs(chunker):
    text = "Перше речення. Друге речення!\n\nТретє речення?"
    sentences = chunker.split_sentences(text)
    assert len(sentences) == 3
