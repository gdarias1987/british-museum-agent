from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from british_museum_agent.ranking.reranker import MultilingualCrossEncoderReranker
from british_museum_agent.retrieval.knowledge_base import KnowledgeDocument, RetrievalStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_QUERY_LENGTH: int = 512  # chars — SentenceTransformer context limit safety
VECTOR_WEIGHT: float = 0.25
RERANKER_WEIGHT: float = 0.75
CANDIDATE_K: int = 10


class VectorStoreUnavailable(RuntimeError):
    pass


class EmbeddingModel(Protocol):
    model_name: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class RerankingModel(Protocol):
    model_name: str

    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class LocalSentenceTransformerEmbeddings:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._get_model().encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@dataclass(frozen=True)
class VectorSearchCandidate:
    document: KnowledgeDocument
    vector_score: float
    reranker_score: float
    final_score: float


class VectorKnowledgeBase:
    def __init__(
        self,
        chroma_path: Path,
        collection_name: str,
        embedding_model: EmbeddingModel,
        reranker: RerankingModel,
        *,
        candidate_k: int = 10,
    ):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.embedding_model = embedding_model
        self.reranker = reranker
        self.candidate_k = candidate_k or CANDIDATE_K
        self._reranker_error: str | None = None
        self._reranker_loaded = False
        try:
            self.client = chromadb.PersistentClient(
                path=str(chroma_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self.collection = self.client.get_collection(collection_name)
        except Exception as exc:
            logger.error("Chroma init failed: %s: %s", type(exc).__name__, exc)
            raise VectorStoreUnavailable(
                f"Chroma index unavailable ({type(exc).__name__}: {exc})."
            ) from exc

        if self.collection.count() == 0:
            raise VectorStoreUnavailable("The Chroma collection exists but is empty.")
        indexed_model = (self.collection.metadata or {}).get("embedding_model")
        if indexed_model and indexed_model != embedding_model.model_name:
            raise VectorStoreUnavailable(
                f"Chroma index was created with model {indexed_model!r}, "
                f"current is {embedding_model.model_name!r}."
            )

    @property
    def status(self) -> RetrievalStatus:
        reranker_active = self._reranker_loaded and self._reranker_error is None
        return RetrievalStatus(
            backend="chroma",
            retrieval_active=True,
            retrieval_detail=f"Persistent Chroma ({self.collection.count()} chunks).",
            reranker="huggingface_cross_encoder" if reranker_active else "vector_score_only",
            reranker_active=reranker_active,
            reranker_detail=(
                f"Model {self.reranker.model_name}."
                if reranker_active
                else (
                    f"Configured with {self.reranker.model_name}; startup warmup is pending."
                    if self._reranker_error is None
                    else f"Reranking unavailable: {self._reranker_error}"
                )
            ),
        )

    def warmup(self) -> None:
        try:
            query = "British Museum startup readiness"
            query_embedding = self.embedding_model.embed_query(query)
            result = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=1,
                include=["documents"],
            )
            documents = (result.get("documents") or [[]])[0]
            if not documents:
                raise VectorStoreUnavailable("Chroma returned no document during warmup.")
            scores = self.reranker.score(query, documents)
            if len(scores) != len(documents):
                self._reranker_error = "reranker invalid warmup result"
                self._reranker_loaded = False
                logger.warning(
                    "Reranker warmup: score count mismatch (%d vs %d)",
                    len(scores),
                    len(documents),
                )
                return
            self._reranker_loaded = True
            self._reranker_error = None
        except VectorStoreUnavailable:
            raise  # Chroma failure → propagate so ResilientKnowledgeBase can fall back
        except Exception as exc:
            self._reranker_loaded = False
            self._reranker_error = type(exc).__name__
            logger.warning("Reranker warmup failed: %s: %s", type(exc).__name__, exc)
            # Don't propagate — Chroma is still usable without reranker

    def search(self, query: str, top_k: int = 4) -> list[tuple[KnowledgeDocument, float]]:
        if not query or not query.strip():
            return []
        if len(query) > MAX_QUERY_LENGTH:
            logger.warning("Query truncated from %d to %d chars", len(query), MAX_QUERY_LENGTH)
            query = query[:MAX_QUERY_LENGTH]
        query_embedding = self.embedding_model.embed_query(query)
        result = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(self.candidate_k, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        if not documents:
            return []

        candidates: list[tuple[KnowledgeDocument, float]] = []
        for text, metadata, distance in zip(documents, metadatas, distances, strict=True):
            metadata = metadata or {}
            document = KnowledgeDocument(
                chunk_id=str(metadata.get("chunk_id", "unknown")),
                title=str(metadata.get("title", "Untitled")),
                source=str(metadata.get("source", "chroma")),
                url=str(metadata.get("source_url")) if metadata.get("source_url") else None,
                text=text,
                tags=_decode_tags(metadata.get("tags")),
            )
            candidates.append((document, _cosine_distance_to_score(float(distance))))

        try:
            reranker_scores = self.reranker.score(query, [doc.text for doc, _ in candidates])
            if len(reranker_scores) != len(candidates):
                raise ValueError(
                    "Reranker returned a different number of scores than candidates."
                )
            self._reranker_loaded = True
            self._reranker_error = None
        except Exception as exc:
            self._reranker_loaded = False
            self._reranker_error = type(exc).__name__
            return sorted(candidates, key=lambda item: item[1], reverse=True)[:top_k]

        ranked = [
            VectorSearchCandidate(
                document=document,
                vector_score=vector_score,
                reranker_score=reranker_score,
                final_score=min(
                    1.0,
                    max(
                        0.0,
                        VECTOR_WEIGHT * vector_score
                        + RERANKER_WEIGHT * reranker_score,
                    ),
                ),
            )
            for (document, vector_score), reranker_score in zip(
                candidates, reranker_scores, strict=True
            )
        ]
        ranked.sort(key=lambda item: item.final_score, reverse=True)
        return [(item.document, item.final_score) for item in ranked[:top_k]]


class ResilientKnowledgeBase:
    def __init__(
        self,
        primary: VectorKnowledgeBase | None,
        fallback,
        reason: str | None = None,
    ):
        self.primary = primary
        self.fallback = fallback
        self._fallback_reason = reason

    @property
    def status(self) -> RetrievalStatus:
        if self.primary is not None and self._fallback_reason is None:
            return self.primary.status
        fallback_status = self.fallback.status
        return RetrievalStatus(
            backend="lexical_fallback",
            retrieval_active=fallback_status.retrieval_active,
            retrieval_detail=(
                f"Lexical fallback active: "
                f"{self._fallback_reason or 'Chroma is unavailable'}"
            ),
            reranker="disabled",
            reranker_active=False,
            reranker_detail="The reranker is not used during lexical fallback.",
        )

    def warmup(self) -> None:
        if self.primary is None:
            self.fallback.warmup()
            return
        try:
            self.primary.warmup()
            self._fallback_reason = None
        except Exception as exc:
            self._fallback_reason = f"startup warmup failed ({type(exc).__name__})"
            self.primary = None
            self.fallback.warmup()

    def search(self, query: str, top_k: int = 4) -> list[tuple[KnowledgeDocument, float]]:
        if self.primary is None:
            return self.fallback.search(query, top_k)
        try:
            matches = self.primary.search(query, top_k)
            self._fallback_reason = None
            return matches
        except Exception as exc:
            self._fallback_reason = f"search failed ({type(exc).__name__})"
            return self.fallback.search(query, top_k)


def _decode_tags(value) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    try:
        parsed = json.loads(str(value))
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed)
    except json.JSONDecodeError:
        pass
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


def _cosine_distance_to_score(distance: float) -> float:
    return min(1.0, max(0.0, 1.0 - distance / 2.0))



