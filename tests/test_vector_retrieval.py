from pathlib import Path
from typing import Sequence

from british_museum_agent.retrieval.corpus import CorpusChunk
from british_museum_agent.retrieval.indexer import build_chroma_index
from british_museum_agent.retrieval.vector_store import VectorKnowledgeBase


class FakeEmbeddings:
    model_name = "fake-multilingual-embeddings"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 1.0]

    @staticmethod
    def _vector(text: str) -> list[float]:
        return [1.0, 0.0] if "Rosetta" in text else [0.0, 1.0]


class FakeReranker:
    model_name = "fake-multilingual-reranker"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [0.95 if "Rosetta" in passage else 0.10 for passage in passages]


def _chunk(chunk_id: str, title: str, text: str) -> CorpusChunk:
    return CorpusChunk(
        chunk_id=chunk_id,
        title=title,
        source=f"{chunk_id}.md",
        text=text,
        url=f"https://example.com/{chunk_id}",
        tags=("es",),
        metadata={
            "chunk_id": chunk_id,
            "title": title,
            "source": f"{chunk_id}.md",
            "source_url": f"https://example.com/{chunk_id}",
            "tags": "[\"es\"]",
        },
    )


def test_persistent_chroma_index_is_idempotent_and_reranks(tmp_path: Path):
    chroma_path = tmp_path / "chroma"
    chunks = [
        _chunk("rosetta-1", "Piedra de Rosetta", "La Piedra de Rosetta está en la Sala 4."),
        _chunk("mesopotamia-1", "Mesopotamia", "La Sala 56 presenta civilizaciones mesopotámicas."),
    ]
    embeddings = FakeEmbeddings()

    first = build_chroma_index(
        chroma_path=chroma_path,
        collection_name="museum_test",
        chunks=chunks,
        fingerprint="same-corpus",
        embedding_model=embeddings,
    )
    second = build_chroma_index(
        chroma_path=chroma_path,
        collection_name="museum_test",
        chunks=chunks,
        fingerprint="same-corpus",
        embedding_model=embeddings,
    )

    assert first.rebuilt is True
    assert second.rebuilt is False
    assert (chroma_path / "index_manifest.json").exists()

    retriever = VectorKnowledgeBase(
        chroma_path,
        "museum_test",
        embeddings,
        FakeReranker(),
        candidate_k=2,
    )
    matches = retriever.search("¿Cuál es la pieza más icónica?", top_k=2)

    assert matches[0][0].chunk_id == "rosetta-1"
    assert matches[0][1] > matches[1][1]
    assert retriever.status.backend == "chroma"
    assert retriever.status.reranker_active is True


class InvalidLengthReranker:
    model_name = "invalid-length-reranker"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [0.5]


def test_invalid_reranker_score_count_falls_back_to_vector_order(tmp_path: Path):
    chroma_path = tmp_path / "chroma"
    chunks = [
        _chunk("rosetta-1", "Piedra de Rosetta", "La Piedra de Rosetta está en la Sala 4."),
        _chunk("mesopotamia-1", "Mesopotamia", "La Sala 56 presenta civilizaciones mesopotámicas."),
    ]
    embeddings = FakeEmbeddings()
    build_chroma_index(
        chroma_path=chroma_path,
        collection_name="museum_test",
        chunks=chunks,
        fingerprint="invalid-reranker-corpus",
        embedding_model=embeddings,
    )
    retriever = VectorKnowledgeBase(
        chroma_path,
        "museum_test",
        embeddings,
        InvalidLengthReranker(),
        candidate_k=2,
    )

    matches = retriever.search("Mesopotamia", top_k=2)

    assert [match[0].chunk_id for match in matches] == [
        "mesopotamia-1",
        "rosetta-1",
    ]
    assert retriever.status.reranker_active is False
    assert retriever.status.reranker_detail == "Reranking unavailable: ValueError"


class RecordingEmbeddings(FakeEmbeddings):
    def __init__(self):
        self.last_query = ""

    def embed_query(self, text: str) -> list[float]:
        self.last_query = text
        return super().embed_query(text)


def test_long_query_is_truncated_before_embedding(tmp_path: Path):
    chroma_path = tmp_path / "chroma"
    embeddings = RecordingEmbeddings()
    chunks = [_chunk("one", "One", "Contenido de prueba")]
    build_chroma_index(
        chroma_path=chroma_path,
        collection_name="museum_test",
        chunks=chunks,
        fingerprint="query-limit-corpus",
        embedding_model=embeddings,
    )
    retriever = VectorKnowledgeBase(
        chroma_path,
        "museum_test",
        embeddings,
        FakeReranker(),
    )

    retriever.search("x" * 600)

    assert len(embeddings.last_query) == 512
