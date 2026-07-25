from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class KnowledgeDocument:
    chunk_id: str
    title: str
    source: str
    text: str
    url: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetrievalStatus:
    backend: str
    retrieval_active: bool
    retrieval_detail: str
    reranker: str
    reranker_active: bool
    reranker_detail: str


class KnowledgeRetriever(Protocol):
    @property
    def status(self) -> RetrievalStatus: ...

    def warmup(self) -> None: ...

    def search(self, query: str, top_k: int = 4) -> list[tuple[KnowledgeDocument, float]]: ...


class KnowledgeBase:
    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.documents = self._load(index_path)

    @property
    def status(self) -> RetrievalStatus:
        return RetrievalStatus(
            backend="lexical_fallback",
            retrieval_active=bool(self.documents),
            retrieval_detail=f"Local JSON index ({len(self.documents)} chunks).",
            reranker="disabled",
            reranker_active=False,
            reranker_detail="The lexical index does not run neural reranking.",
        )

    def warmup(self) -> None:
        return None

    def _load(self, index_path: Path) -> list[KnowledgeDocument]:
        if not index_path.exists():
            return []
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        return [
            KnowledgeDocument(**{**item, "tags": tuple(item.get("tags", []))})
            for item in raw
        ]

    def search(self, query: str, top_k: int = 4) -> list[tuple[KnowledgeDocument, float]]:
        if not self.documents:
            return []
        base_query_terms = set(_tokenize(query))
        query_terms = set(_tokenize(_expand_query(query)))
        if not base_query_terms:
            return []

        scored: list[tuple[KnowledgeDocument, float]] = []
        for doc in self.documents:
            haystack_terms = set(_tokenize(" ".join([doc.title, doc.text, " ".join(doc.tags)])))
            overlap = len(query_terms & haystack_terms)
            if overlap == 0:
                continue
            score = min(1.0, overlap / max(2, min(len(base_query_terms), 6)))
            scored.append((doc, score))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]


def _expand_query(text: str) -> str:
    normalized = _strip_accents(text).lower()
    additions: list[str] = []
    synonym_map = {
        "egipto": "egypt egyptian",
        "egipcio": "egypt egyptian",
        "egipcia": "egypt egyptian",
        "medio oriente": "middle east",
        "oriente medio": "middle east",
        "sala": "room gallery",
        "salas": "rooms galleries",
        "recorrido": "route tour trail",
        "recorridos": "routes tours trails",
        "recomenda": "recommend route tour trail",
        "accesibilidad": "accessibility accessible",
        "accesible": "accessibility accessible",
        "abierta": "open status",
        "abierto": "open status",
    }
    for needle, expansion in synonym_map.items():
        if needle in normalized:
            additions.append(expansion)
    return f"{text} {' '.join(additions)}"


def _tokenize(text: str) -> list[str]:
    normalized = _strip_accents(text)
    tokens = re.findall(r"[A-Za-z0-9]+", normalized)
    return [token.lower() for token in tokens if len(token) > 2 or token.isdigit()]


def _strip_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
